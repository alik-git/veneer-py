"""Tests for veneer config parsing — self-contained format, no extends/stack."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from veneer.config import VeneerError, load_config

if TYPE_CHECKING:
    from pathlib import Path


def write_config(root: Path, text: str) -> None:
    """Write a veneer config for a test repo."""
    (root / "veneer.toml").write_text(text, encoding="utf-8")


def test_load_minimal_config(tmp_path: Path) -> None:
    """Load the smallest supported config."""
    write_config(
        tmp_path,
        """
        [python]
        base_conda_env = "example"

        [editables]
        packages = ["."]
        """,
    )

    config = load_config(tmp_path)

    assert config.project_root == tmp_path
    assert config.config_path == tmp_path / "veneer.toml"
    assert config.command_cwd == tmp_path
    assert config.base_conda_env == "example"
    assert config.venv == tmp_path / ".venv"
    assert config.editable_packages == (tmp_path,)
    assert config.install_editable_deps is False


def test_editables_can_point_to_sibling_worktrees(tmp_path: Path) -> None:
    """Allow editable packages outside the owning git root."""
    write_config(
        tmp_path,
        """
        [python]
        base_conda_env = "example"

        [editables]
        packages = ["../sibling"]
        """,
    )

    config = load_config(tmp_path)

    assert config.editable_packages == ((tmp_path / "../sibling").resolve(),)


def test_editables_can_use_absolute_paths(tmp_path: Path) -> None:
    """Allow editable packages to point at canonical checkouts."""
    package = tmp_path / "repos" / "package"
    write_config(
        tmp_path,
        f"""
        [python]
        base_conda_env = "example"

        [editables]
        packages = [{str(package)!r}]
        """,
    )

    config = load_config(tmp_path)

    assert config.editable_packages == (package,)


def test_editables_can_use_tilde_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow editable packages to use user-home anchored paths."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    write_config(
        tmp_path,
        """
        [python]
        base_conda_env = "example"

        [editables]
        packages = ["~/repos/package"]
        """,
    )

    config = load_config(tmp_path)

    assert config.editable_packages == (home / "repos" / "package",)


def test_venv_must_stay_inside_project_root(tmp_path: Path) -> None:
    """Reject venv paths outside the project root."""
    write_config(
        tmp_path,
        """
        [python]
        base_conda_env = "example"
        venv = "../outside"
        """,
    )

    with pytest.raises(VeneerError, match="python.venv must stay inside"):
        load_config(tmp_path)


def test_absolute_venv_can_point_inside_project_root(tmp_path: Path) -> None:
    """Allow absolute venv paths if they stay inside the project root."""
    venv = tmp_path / ".veneer" / ".venv"
    write_config(
        tmp_path,
        f"""
        [python]
        base_conda_env = "example"
        venv = {str(venv)!r}
        """,
    )

    config = load_config(tmp_path)

    assert config.venv == venv


def test_extends_is_rejected_with_migration_hint(tmp_path: Path) -> None:
    """Reject old pointer configs with a clear migration message."""
    write_config(
        tmp_path,
        """
        [veneer]
        extends = "../veneer.mlab.toml"
        """,
    )

    with pytest.raises(VeneerError, match="no longer supported"):
        load_config(tmp_path)


def test_missing_config_has_clear_error(tmp_path: Path) -> None:
    """Explain how to create the required config."""
    with pytest.raises(VeneerError, match="missing veneer.toml"):
        load_config(tmp_path)
