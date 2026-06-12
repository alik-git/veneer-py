"""Configuration loading for veneer."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class VeneerError(Exception):
    """User-facing veneer error."""


@dataclass(frozen=True)
class VeneerConfig:
    """Parsed veneer configuration."""

    project_root: Path
    config_path: Path
    command_cwd: Path
    base_conda_env: str
    venv: Path
    editable_packages: tuple[Path, ...]
    install_editable_deps: bool


def load_config(root: Path) -> VeneerConfig:
    """Load and validate ``veneer.toml`` from a git worktree root."""
    project_root = root.resolve()
    config_path = project_root / "veneer.toml"
    if not config_path.is_file():
        raise VeneerError(
            "missing veneer.toml at git root:\n"
            f"  {config_path}\n\n"
            "Create veneer.toml with:\n\n"
            "[python]\n"
            'base_conda_env = "your-conda-env"\n\n'
            "[editables]\n"
            'packages = ["."]',
        )

    raw = _load_toml(config_path)

    if _table(raw, "veneer", required=False).get("extends"):
        raise VeneerError(
            "veneer.toml [veneer].extends is no longer supported.\n"
            "Replace the pointer config with a self-contained veneer.toml:\n\n"
            "[python]\n"
            'base_conda_env = "your-conda-env"\n\n'
            "[editables]\n"
            'packages = ["source/your_package"]',
        )

    python = _table(raw, "python")
    base_conda_env = _required_nonempty_string(python, "base_conda_env")
    venv_value = _optional_nonempty_string(python, "venv", default=".venv")
    venv = _resolve_venv_path(project_root, venv_value)

    editables = _table(raw, "editables", required=False)
    editable_values = _optional_string_list(editables, "packages")
    install_deps = _optional_bool(editables, "install_deps", default=False)
    editable_packages = tuple(
        _resolve_path(project_root, v) for v in editable_values
    )

    return VeneerConfig(
        project_root=project_root,
        config_path=config_path,
        command_cwd=project_root,
        base_conda_env=base_conda_env,
        venv=venv,
        editable_packages=editable_packages,
        install_editable_deps=install_deps,
    )


def _load_toml(config_path: Path) -> dict[str, Any]:
    """Load a TOML config file with user-facing errors."""
    try:
        with config_path.open("rb") as file:
            raw = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise VeneerError(f"invalid veneer.toml at {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise VeneerError("veneer.toml must contain TOML tables")
    return raw


def _table(raw: dict[str, Any], key: str, *, required: bool = True) -> dict[str, Any]:
    value = raw.get(key)
    if value is None:
        if required:
            raise VeneerError(f"veneer.toml missing [{key}] table")
        return {}
    if not isinstance(value, dict):
        raise VeneerError(f"veneer.toml [{key}] must be a table")
    return value


def _required_nonempty_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VeneerError(f"veneer.toml requires non-empty string: {key}")
    return value.strip()


def _optional_nonempty_string(
    raw: dict[str, Any],
    key: str,
    *,
    default: str,
) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise VeneerError(f"veneer.toml field must be a non-empty string: {key}")
    return value.strip()


def _optional_string_list(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
        raise VeneerError(f"veneer.toml field must be a list of strings: {key}")
    return tuple(i for i in value if i.strip())


def _optional_bool(raw: dict[str, Any], key: str, *, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise VeneerError(f"veneer.toml field must be a boolean: {key}")
    return value


def _resolve_venv_path(project_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    resolved = path.resolve() if path.is_absolute() else (project_root / path).resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise VeneerError(
            f"veneer.toml python.venv must stay inside the project root:\n"
            f"  venv: {resolved}\n"
            f"  project root: {project_root}",
        ) from exc
    return resolved


def _resolve_path(config_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (config_root / path).resolve()
