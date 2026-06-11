# veneer

Lightweight overlay venv manager for Python worktrees with shared conda bases.

veneer sits as a thin layer between a heavy shared conda environment and the
editable packages you actually work on day-to-day — like a veneer over the
underlying structure. It is designed for setups where conda owns the base
(e.g. IsaacSim, heavy GPU stacks) but you want fast, worktree-local editable
installs without duplicating the full environment.

## Installation

```bash
uv tool install veneer-py
```

Or install from source:

```bash
uv tool install --editable path/to/veneer-py
```

## Usage

```bash
veneer --help
```

## Development

```bash
uv sync --extra dev
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

## Publishing

Releases are published to PyPI automatically when a GitHub Release is created.
