"""Tests for veneer command behavior."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import ANY

import pytest

from veneer.cli import (
    _CONDA_LAUNCHER,
    clean,
    conda_run_command,
    ensure_venv,
    find_git_root,
    print_info,
    read_exit_status,
    reject_editable_pip_install,
    run_conda_overlay,
    run_passthrough,
    update_editables,
    veneer_env,
)
from veneer.config import VeneerConfig, VeneerError


def config(root: Path, *, editables: tuple[Path, ...] = ()) -> VeneerConfig:
    """Build a test config."""
    return VeneerConfig(
        project_root=root,
        entry_config_path=root / "veneer.toml",
        config_path=root / "veneer.toml",
        config_root=root,
        env_root=root,
        command_cwd=root,
        base_conda_env="base-env",
        venv=root / ".venv",
        editable_packages=editables,
        install_editable_deps=False,
    )


def stack_config(root: Path, *, editables: tuple[Path, ...] = ()) -> VeneerConfig:
    """Build a test stack config."""
    project_root = root / "minerva_lab"
    env_root = root
    return VeneerConfig(
        project_root=project_root,
        entry_config_path=project_root / "veneer.toml",
        config_path=root / "veneer.mlab.toml",
        config_root=root,
        env_root=env_root,
        command_cwd=project_root,
        base_conda_env="mlab-shared",
        venv=env_root / ".veneer" / "mlab" / ".venv",
        editable_packages=editables,
        install_editable_deps=False,
        config_kind="stack",
    )


def write_launcher_status(args: list[str], returncode: int) -> None:
    """Write the child status path embedded in a conda launcher command."""
    Path(args[10]).write_text(f"{returncode}\n", encoding="utf-8")


def test_find_git_root_uses_git_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Find the git root through git rather than directory scanning."""

    def fake_run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert args == ["git", "rev-parse", "--show-toplevel"]
        assert kwargs["cwd"] == tmp_path
        return subprocess.CompletedProcess(args, 0, stdout=f"{tmp_path}\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert find_git_root(tmp_path) == tmp_path.resolve()


def test_find_git_root_fails_clearly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail quickly outside git worktrees."""

    def fake_run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 128, stdout="", stderr="no git")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(VeneerError, match="not inside a git worktree"):
        find_git_root(tmp_path)


def test_ensure_venv_creates_overlay_with_conda(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create a system-site-packages venv from the configured conda env."""
    calls: list[list[str]] = []
    monkeypatch.setenv("CONDA_EXE", "/opt/conda/bin/conda")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert kwargs["cwd"] == tmp_path
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    ensure_venv(config(tmp_path))

    assert calls == [
        [
            "/opt/conda/bin/conda",
            "run",
            "-n",
            "base-env",
            "--no-capture-output",
            "python",
            "-m",
            "venv",
            "--system-site-packages",
            str(tmp_path / ".venv"),
        ],
    ]


def test_update_editables_installs_no_deps_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Install configured editable packages with no dependency solving."""
    package = tmp_path / "package"
    package.mkdir()
    status_path = tmp_path / "status.txt"
    calls: list[list[str]] = []
    monkeypatch.setenv("CONDA_EXE", "/opt/conda/bin/conda")
    monkeypatch.setattr("veneer.cli.make_status_path", lambda: status_path)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"]["PATH"].split(":")[0] == str(tmp_path / ".venv" / "bin")
        write_launcher_status(args, 0)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    update_editables(config(tmp_path, editables=(package,)))

    assert calls == [
        [
            "/opt/conda/bin/conda",
            "run",
            "-n",
            "base-env",
            "--no-capture-output",
            "--",
            "python",
            "-c",
            ANY,
            str(tmp_path / ".venv" / "bin"),
            str(status_path),
            str(tmp_path / ".venv" / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "-e",
            str(package),
        ],
    ]


def test_passthrough_runs_inside_conda_with_venv_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run normal commands with conda activation and the worktree overlay."""
    status_path = tmp_path / "status.txt"
    calls: list[list[str]] = []
    monkeypatch.setenv("CONDA_EXE", "/opt/conda/bin/conda")
    monkeypatch.setattr("veneer.cli.make_status_path", lambda: status_path)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"]["PATH"].split(":")[0] == str(tmp_path / ".venv" / "bin")
        write_launcher_status(args, 7)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    returncode = run_passthrough(config(tmp_path), ["pytest", "-q"])

    assert returncode == 7
    assert calls == [
        [
            "/opt/conda/bin/conda",
            "run",
            "-n",
            "base-env",
            "--no-capture-output",
            "--",
            "python",
            "-c",
            ANY,
            str(tmp_path / ".venv" / "bin"),
            str(status_path),
            "pytest",
            "-q",
        ],
    ]


def test_run_conda_overlay_reports_conda_infrastructure_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat missing status plus failed conda as an infrastructure failure."""
    status_path = tmp_path / "status.txt"
    monkeypatch.setenv("CONDA_EXE", "/opt/conda/bin/conda")
    monkeypatch.setattr("veneer.cli.make_status_path", lambda: status_path)

    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 127)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(VeneerError, match="conda failed before"):
        run_conda_overlay(config(tmp_path), ["python", "--version"])


def test_run_conda_overlay_requires_launcher_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat missing status plus successful conda as a veneer launcher bug."""
    status_path = tmp_path / "status.txt"
    monkeypatch.setenv("CONDA_EXE", "/opt/conda/bin/conda")
    monkeypatch.setattr("veneer.cli.make_status_path", lambda: status_path)

    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(VeneerError, match="did not report"):
        run_conda_overlay(config(tmp_path), ["python", "--version"])


def test_update_editables_raises_child_failure_without_conda_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report pip failures from the launcher status file."""
    package = tmp_path / "package"
    package.mkdir()
    status_path = tmp_path / "status.txt"
    monkeypatch.setenv("CONDA_EXE", "/opt/conda/bin/conda")
    monkeypatch.setattr("veneer.cli.make_status_path", lambda: status_path)

    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        write_launcher_status(args, 9)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(VeneerError, match="exit code 9"):
        update_editables(config(tmp_path, editables=(package,)))


def test_conda_run_command_separates_conda_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Separate conda options from the command being run."""
    monkeypatch.setenv("CONDA_EXE", "/opt/conda/bin/conda")
    status_path = tmp_path / "status.txt"

    assert conda_run_command(
        config(tmp_path),
        ["python", "--version"],
        status_path=status_path,
    ) == [
        "/opt/conda/bin/conda",
        "run",
        "-n",
        "base-env",
        "--no-capture-output",
        "--",
        "python",
        "-c",
        ANY,
        str(tmp_path / ".venv" / "bin"),
        str(status_path),
        "python",
        "--version",
    ]


def test_launcher_records_child_exit_without_failing(tmp_path: Path) -> None:
    """Keep conda successful while recording the child command's failure code."""
    status_path = tmp_path / "status.txt"

    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            _CONDA_LAUNCHER,
            str(tmp_path),
            str(status_path),
            sys.executable,
            "-c",
            "raise SystemExit(7)",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert read_exit_status(status_path) == 7


def test_launcher_records_missing_command_as_127(tmp_path: Path) -> None:
    """Return command-not-found through the status file, not launcher failure."""
    status_path = tmp_path / "status.txt"

    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            _CONDA_LAUNCHER,
            str(tmp_path),
            str(status_path),
            "definitely-not-a-veneer-test-command",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "command not found" in completed.stderr
    assert read_exit_status(status_path) == 127


def test_clean_removes_venv(tmp_path: Path) -> None:
    """Remove the configured worktree venv."""
    venv = tmp_path / ".venv"
    venv.mkdir()

    clean(config(tmp_path))

    assert not venv.exists()


def test_clean_refuses_shared_stack_venv_by_default(tmp_path: Path) -> None:
    """Avoid deleting a shared stack venv without an explicit flag."""
    cfg = stack_config(tmp_path)
    cfg.venv.mkdir(parents=True)

    with pytest.raises(VeneerError, match="refusing to remove shared stack venv"):
        clean(cfg)

    assert cfg.venv.exists()


def test_clean_shared_removes_shared_stack_venv(tmp_path: Path) -> None:
    """Remove a shared stack venv only when explicitly requested."""
    cfg = stack_config(tmp_path)
    cfg.venv.mkdir(parents=True)

    clean(cfg, allow_shared=True)

    assert not cfg.venv.exists()


def test_veneer_env_prefers_venv_bin(tmp_path: Path) -> None:
    """Put the worktree venv first on PATH."""
    env = veneer_env(config(tmp_path))

    assert env["PATH"].split(":")[0] == str(tmp_path / ".venv" / "bin")
    assert env["PYTHONNOUSERSITE"] == "1"


def test_print_info_includes_root_and_shared_venv_details(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Expose enough path detail to diagnose stack config mistakes."""
    cfg = stack_config(tmp_path)

    print_info(cfg)

    out = capsys.readouterr().out
    assert f"project root: {tmp_path / 'minerva_lab'}" in out
    assert f"entry config: {tmp_path / 'minerva_lab' / 'veneer.toml'}" in out
    assert f"effective config: {tmp_path / 'veneer.mlab.toml'}" in out
    assert "config kind: stack" in out
    assert "shared venv: yes" in out
    assert f"env root: {tmp_path}" in out
    assert f"command cwd: {tmp_path / 'minerva_lab'}" in out


@pytest.mark.parametrize(
    "args",
    [
        ["pip", "install", "-e", "."],
        ["pip", "install", "--editable", "."],
        ["python", "-m", "pip", "install", "-e", "."],
        ["python", "-m", "pip", "install", "--editable", "."],
    ],
)
def test_reject_editable_pip_install(args: list[str]) -> None:
    """Reject editable installs outside veneer.toml."""
    with pytest.raises(VeneerError, match="veneer update-editables"):
        reject_editable_pip_install(args)


@pytest.mark.parametrize(
    "args",
    [
        ["pip", "install", "requests"],
        ["python", "-m", "pip", "install", "requests"],
        ["pytest"],
    ],
)
def test_allow_non_editable_passthrough_commands(args: list[str]) -> None:
    """Allow ordinary passthrough commands."""
    reject_editable_pip_install(args)
