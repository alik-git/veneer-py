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
    entry_config_path: Path
    config_path: Path
    config_root: Path
    env_root: Path
    command_cwd: Path
    base_conda_env: str
    venv: Path
    editable_packages: tuple[Path, ...]
    install_editable_deps: bool
    config_kind: str = "repo"

    @property
    def uses_shared_venv(self) -> bool:
        """Return whether the configured venv may be shared by multiple repos."""
        return self.config_kind == "stack"


def load_config(root: Path) -> VeneerConfig:
    """Load and validate ``veneer.toml`` from a git worktree root."""
    project_root = root.resolve()
    entry_config_path = project_root / "veneer.toml"
    if not entry_config_path.is_file():
        raise VeneerError(
            "missing veneer.toml at git root:\n"
            f"  {entry_config_path}\n\n"
            "Create veneer.toml with:\n\n"
            "[python]\n"
            'base_conda_env = "your-conda-env"\n\n'
            "[editables]\n"
            'packages = ["."]',
        )

    raw = _load_toml(entry_config_path)
    veneer_section = _table(raw, "veneer", required=False)
    extends = _optional_string(veneer_section, "extends")
    if extends is not None:
        return _load_extending_config(
            project_root=project_root,
            entry_config_path=entry_config_path,
            raw=raw,
            extends=extends,
        )

    if veneer_section:
        raise VeneerError(
            "veneer.toml [veneer] table is only supported for explicit extends configs",
        )

    return _parse_effective_config(
        project_root=project_root,
        entry_config_path=entry_config_path,
        config_path=entry_config_path,
        config_kind="repo",
    )


def _load_extending_config(
    *,
    project_root: Path,
    entry_config_path: Path,
    raw: dict[str, Any],
    extends: str,
) -> VeneerConfig:
    _require_keys(raw, {"veneer"}, context="pointer veneer.toml")
    _require_keys(raw["veneer"], {"extends"}, context="pointer veneer.toml [veneer]")

    config_path = _resolve_config_path(entry_config_path.parent, extends)
    if config_path == entry_config_path:
        raise VeneerError("veneer.toml [veneer].extends cannot point to itself")
    if not config_path.is_file():
        raise VeneerError(
            f"veneer.toml [veneer].extends target does not exist: {config_path}",
        )

    effective_raw = _load_toml(config_path)
    effective_veneer = _table(effective_raw, "veneer")
    _require_keys(effective_veneer, {"kind"}, context=f"{config_path} [veneer]")
    kind = _required_nonempty_string(effective_veneer, "kind")
    if kind != "stack":
        raise VeneerError(
            f'{config_path} [veneer].kind must be "stack" for explicit extends',
        )

    return _parse_effective_config(
        project_root=project_root,
        entry_config_path=entry_config_path,
        config_path=config_path,
        config_kind="stack",
        raw=effective_raw,
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


def _parse_effective_config(
    *,
    project_root: Path,
    entry_config_path: Path,
    config_path: Path,
    config_kind: str,
    raw: dict[str, Any] | None = None,
) -> VeneerConfig:
    """Parse an effective repo or stack config into a concrete config."""
    config_root = config_path.parent
    env_root = config_root
    raw = _load_toml(config_path) if raw is None else raw
    python = _table(raw, "python")
    base_conda_env = _required_nonempty_string(python, "base_conda_env")
    venv_value = _optional_nonempty_string(python, "venv", default=".venv")
    venv = _resolve_venv_path(
        config_root,
        env_root,
        venv_value,
        field="python.venv",
    )

    editables = _table(raw, "editables", required=False)
    editable_values = _optional_string_list(editables, "packages")
    install_deps = _optional_bool(editables, "install_deps", default=False)

    editable_packages = tuple(
        _resolve_path(config_root, value) for value in editable_values
    )

    return VeneerConfig(
        project_root=project_root,
        entry_config_path=entry_config_path,
        config_path=config_path,
        config_root=config_root,
        env_root=env_root,
        command_cwd=project_root,
        base_conda_env=base_conda_env,
        venv=venv,
        editable_packages=editable_packages,
        install_editable_deps=install_deps,
        config_kind=config_kind,
    )


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


def _optional_string(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise VeneerError(f"veneer.toml field must be a non-empty string: {key}")
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
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise VeneerError(f"veneer.toml field must be a list of strings: {key}")
    return tuple(item for item in value if item.strip())


def _optional_bool(raw: dict[str, Any], key: str, *, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise VeneerError(f"veneer.toml field must be a boolean: {key}")
    return value


def _require_keys(raw: dict[str, Any], allowed: set[str], *, context: str) -> None:
    unexpected = sorted(set(raw) - allowed)
    if unexpected:
        joined = ", ".join(unexpected)
        raise VeneerError(f"{context} has unsupported keys: {joined}")


def _resolve_config_path(config_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (config_root / path).resolve()


def _resolve_venv_path(
    config_root: Path,
    env_root: Path,
    value: str,
    *,
    field: str,
) -> Path:
    path = Path(value).expanduser()
    resolved = path.resolve() if path.is_absolute() else (config_root / path).resolve()
    resolved_env_root = env_root.resolve()
    try:
        resolved.relative_to(resolved_env_root)
    except ValueError as exc:
        raise VeneerError(
            f"veneer.toml {field} must stay inside the env root:\n"
            f"  venv: {resolved}\n"
            f"  env root: {resolved_env_root}",
        ) from exc
    return resolved


def _resolve_path(config_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (config_root / path).resolve()
