# Contributing

## Requirements

- Python 3.10 or newer
- [uv](https://docs.astral.sh/uv/) for dependency management

## Setup

Clone the repository and install all dependencies including dev tools:

```bash
git clone https://github.com/openfaas/python-sdk
cd python-sdk
uv sync
```

## Running tests

```bash
uv run pytest tests/
```

## Linting

Check for lint issues:

```bash
uv run ruff check .
```

Fix lint issues automatically:

```bash
uv run ruff check --fix .
```

## Formatting

Check formatting:

```bash
uv run ruff format --check .
```

Apply formatting:

```bash
uv run ruff format .
```

## Type checking

```bash
uv run pyright
```

## CI

All of the above are run automatically on every push and pull request via
GitHub Actions. The workflow runs:

- **Lint** — `ruff check` and `ruff format --check`
- **Type check** — `pyright`
- **Test** — `pytest` across Python 3.10, 3.11, 3.12, and 3.13
