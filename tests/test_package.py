"""Basic smoke tests for veneer."""

from __future__ import annotations

import veneer


def test_import() -> None:
    """Veneer can be imported."""
    assert veneer is not None
