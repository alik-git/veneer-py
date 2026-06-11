"""Command-line interface for veneer."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from veneer.config import VeneerConfig, VeneerError, load_config

if TYPE_CHECKING:
    from collections.abc import Sequence

_CONDA_LAUNCHER = """
import os
import subprocess
import sys

venv_bin = sys.argv[1]
status_path = sys.argv[2]
command = sys.argv[3:]
env = os.environ.copy()
env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
env["PYTHONNOUSERSITE"] = "1"

def write_status(returncode):
    normalized = returncode
    if normalized < 0:
        normalized = 128 + abs(normalized)
    tmp_path = status_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        file.write(f"{normalized}\\n")
    os.replace(tmp_path, status_path)

try:
    completed = subprocess.run(command, env=env)
except FileNotFoundError:
    print(f"veneer: command not found: {command[0]}", file=sys.stderr)
    write_status(127)
except KeyboardInterrupt:
    write_status(130)
else:
    write_status(completed.returncode)
""".strip()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the veneer command-line interface."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main(args)
    except VeneerError as exc:
        print(f"veneer: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def _main(args: list[str]) -> int:
    if not args or args[0] in {"-h", "--help"}:
        _print_help()
        return 0

    root = find_git_root(Path.cwd())
    config = load_config(root)

    command = args[0]

    if command == "info":
        print_info(config)
        return 0
    if command == "update-editables":
        ensure_venv(config)
        update_editables(config)
        return 0
    if command == "clean":
        clean(config, allow_shared=parse_clean_args(args[1:]))
        return 0

    reject_editable_pip_install(args)
    ensure_venv(config)
    return run_passthrough(config, args)


def find_git_root(cwd: Path) -> Path:
    """Find the current git worktree root."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise VeneerError("not inside a git worktree")

    root = Path(result.stdout.strip())
    if not root:
        raise VeneerError("git did not return a worktree root")
    return root.resolve()


def ensure_venv(config: VeneerConfig) -> None:
    """Create the worktree virtual environment if it is missing."""
    python = config.venv / "bin" / "python"
    if python.is_file():
        return

    config.venv.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"Creating {display_path(config.venv, root=config.env_root)} from conda env: "
        f"{config.base_conda_env}",
        file=sys.stderr,
    )
    run_checked(
        [
            conda_executable(),
            "run",
            "-n",
            config.base_conda_env,
            "--no-capture-output",
            "python",
            "-m",
            "venv",
            "--system-site-packages",
            str(config.venv),
        ],
        cwd=config.env_root,
    )


def update_editables(config: VeneerConfig) -> None:
    """Install configured editable packages into the worktree venv."""
    if not config.editable_packages:
        print("No editable packages configured.")
        return

    for package in config.editable_packages:
        if not package.exists():
            raise VeneerError(f"editable package path does not exist: {package}")

    command = [str(config.venv / "bin" / "python"), "-m", "pip", "install"]
    if not config.install_editable_deps:
        command.append("--no-deps")

    for package in config.editable_packages:
        command.extend(["-e", str(package)])

    returncode = run_conda_overlay(config, command)
    if returncode != 0:
        raise VeneerError(
            f"command failed with exit code {returncode}: {_quote(command)}",
        )


def clean(config: VeneerConfig, *, allow_shared: bool = False) -> None:
    """Remove the worktree virtual environment."""
    if config.uses_shared_venv and not allow_shared:
        raise VeneerError(
            "refusing to remove shared stack venv without --shared:\n"
            f"  env root: {config.env_root}\n"
            f"  venv: {config.venv}\n\n"
            "Run `veneer clean --shared` if you intentionally want to remove it.",
        )
    if not config.venv.exists():
        print(f"Already clean: {display_path(config.venv, root=config.env_root)}")
        return
    if not config.venv.is_dir():
        raise VeneerError(f"venv path exists but is not a directory: {config.venv}")
    shutil.rmtree(config.venv)
    print(f"Removed {display_path(config.venv, root=config.env_root)}")


def print_info(config: VeneerConfig) -> None:
    """Print the active veneer configuration."""
    print(f"project root: {config.project_root}")
    print(f"entry config: {config.entry_config_path}")
    print(f"effective config: {config.config_path}")
    print(f"config kind: {config.config_kind}")
    print(f"shared venv: {yes_no(config.uses_shared_venv)}")
    print(f"config root: {config.config_root}")
    print(f"env root: {config.env_root}")
    print(f"command cwd: {config.command_cwd}")
    print(f"base conda env: {config.base_conda_env}")
    print(f"venv: {config.venv}")
    print(f"venv exists: {config.venv.is_dir()}")
    print(f"python: {config.venv / 'bin' / 'python'}")
    print(f"pip: {config.venv / 'bin' / 'pip'}")
    print(f"install editable deps: {config.install_editable_deps}")
    print("editable packages:")
    if config.editable_packages:
        for package in config.editable_packages:
            print(f"  - {package}")
    else:
        print("  - none")


def run_passthrough(config: VeneerConfig, args: list[str]) -> int:
    """Run a normal command inside conda with the worktree venv first on PATH."""
    return run_conda_overlay(config, args)


def run_conda_overlay(config: VeneerConfig, args: list[str]) -> int:
    """Run a command through conda and return the child command exit status."""
    status_path = make_status_path()
    try:
        try:
            command = conda_run_command(config, args, status_path=status_path)
            completed = subprocess.run(  # noqa: S603
                command,
                cwd=config.command_cwd,
                env=veneer_env(config),
                check=False,
            )
        except FileNotFoundError as exc:
            raise VeneerError(f"command not found: {command[0]}") from exc

        if status_path.is_file():
            return read_exit_status(status_path)
        if completed.returncode != 0:
            raise VeneerError(
                "conda failed before the veneer command ran with exit code "
                f"{completed.returncode}:\n"
                f"  conda env: {config.base_conda_env}\n"
                f"  command: {_quote(args)}",
            )
        raise VeneerError(
            "veneer launcher did not report the command exit status",
        )
    finally:
        status_path.unlink(missing_ok=True)
        status_path.with_suffix(status_path.suffix + ".tmp").unlink(missing_ok=True)


def conda_run_command(
    config: VeneerConfig,
    args: list[str],
    *,
    status_path: Path,
) -> list[str]:
    """Wrap a command so conda activation applies before the venv overlay."""
    return [
        conda_executable(),
        "run",
        "-n",
        config.base_conda_env,
        "--no-capture-output",
        "--",
        "python",
        "-c",
        _CONDA_LAUNCHER,
        str(config.venv / "bin"),
        str(status_path),
        *args,
    ]


def reject_editable_pip_install(args: list[str]) -> None:
    """Reject ad hoc editable installs managed outside veneer.toml."""
    if not _is_pip_install(args):
        return
    if not _contains_editable_flag(args):
        return

    raise VeneerError(
        "editable installs must be declared in veneer.toml and installed with "
        "`veneer update-editables`; do not run ad hoc `pip install -e ...` "
        "through veneer",
    )


def parse_clean_args(args: list[str]) -> bool:
    """Parse arguments for ``veneer clean``."""
    allow_shared = False
    for arg in args:
        if arg == "--shared":
            allow_shared = True
            continue
        raise VeneerError(f"unknown clean option: {arg}")
    return allow_shared


def _is_pip_install(args: list[str]) -> bool:
    if len(args) >= 2 and args[0] == "pip" and args[1] == "install":
        return True
    return (
        len(args) >= 4
        and args[0] == "python"
        and args[1:4]
        == [
            "-m",
            "pip",
            "install",
        ]
    )


def _contains_editable_flag(args: list[str]) -> bool:
    return any(arg == "-e" or arg == "--editable" for arg in args)


def run_checked(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> None:
    """Run a command and raise a user-facing error on failure."""
    try:
        subprocess.run(args, cwd=cwd, env=env, check=True)  # noqa: S603
    except FileNotFoundError as exc:
        raise VeneerError(f"command not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise VeneerError(
            f"command failed with exit code {exc.returncode}: {_quote(args)}",
        ) from exc


def veneer_env(config: VeneerConfig) -> dict[str, str]:
    """Build the environment used for veneer-managed commands."""
    env = os.environ.copy()
    bin_dir = str(config.venv / "bin")
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def make_status_path() -> Path:
    """Allocate a temporary path for the conda launcher status file."""
    handle, name = tempfile.mkstemp(prefix="veneer-status-", suffix=".txt")
    os.close(handle)
    path = Path(name)
    path.unlink()
    return path


def read_exit_status(status_path: Path) -> int:
    """Read and validate a child command exit status."""
    text = status_path.read_text(encoding="utf-8").strip()
    try:
        returncode = int(text)
    except ValueError as exc:
        raise VeneerError(f"invalid veneer launcher exit status: {text!r}") from exc
    if returncode < 0:
        raise VeneerError(f"invalid veneer launcher exit status: {returncode}")
    return returncode


def display_path(path: Path, *, root: Path) -> str:
    """Display a path relative to a root when possible."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def yes_no(value: bool) -> str:
    """Render a boolean for human-readable command output."""
    return "yes" if value else "no"


def conda_executable() -> str:
    """Find the conda executable."""
    if conda_exe := os.environ.get("CONDA_EXE"):
        return conda_exe
    default = Path.home() / "miniconda3" / "bin" / "conda"
    if default.is_file():
        return str(default)
    conda = shutil.which("conda")
    if conda:
        return conda
    raise VeneerError("could not find conda; set CONDA_EXE or put conda on PATH")


def _print_help() -> None:
    parser = argparse.ArgumentParser(
        prog="veneer",
        description="Run Python commands through a worktree-local .venv overlay.",
    )
    parser.add_argument(
        "command",
        nargs="*",
        help="info, update-editables, clean, or a command",
    )
    parser.print_help()
    print(
        "\nExamples:\n"
        "  veneer info\n"
        "  veneer update-editables\n"
        "  veneer clean\n"
        "  veneer python -m pytest",
    )


def _quote(args: Sequence[str]) -> str:
    return " ".join(args)
