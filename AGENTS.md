# AGENTS.md

Repo-specific instructions for agents working with the `veneer` package.

## Package Conventions

- Keep importable package code under `src/veneer/`.
- Put CLI entrypoint behavior in `src/veneer/cli.py`.

## Validation

- Run checks relevant to the files changed.
- For README/docs-only changes, run formatting and pre-commit hygiene.
- For Python or packaging changes, also run Ruff, mypy, pytest, and build
  checks.

## Pull Requests

- Use `.github/PULL_REQUEST_TEMPLATE.md` for PR descriptions.
- Do not commit secrets, credentials, local runtime config, or scratch notes.
- Keep `AGENTS.md` updated when recurring agent instructions become
  repo-specific.
