# Module 0 🥜

## What has been done so far

- Created the base folder structure: `src/`, `tests/`, `configs/`, `docs/`, `scripts/`
- `src/main.py` — app entry point
- `tests/test_placeholder.py` — two initial tests to confirm the runner works
- `Makefile` — shortcuts for `run`, `test`, and `lint`

## Current project structure

```text
trade/
  configs/
  docs/
  scripts/
  src/
    __init__.py
    main.py
  tests/
    __init__.py
    test_placeholder.py
  .env.example
  .gitignore
  .pre-commit-config.yaml
  .secrets.baseline
  Makefile
  README.md
```

## How to run

```powershell
make run
```

If `make` is not available on your machine, run:

```powershell
python src/main.py
```

## How to test

```powershell
make test
```

Or directly:

```powershell
pytest tests/ -v
```

## Tests

Two placeholder tests in `tests/test_placeholder.py`:
- `test_sanity` — verifies the test runner works
- `test_negative_input` — example of a negative test (bad input raises ValueError)

## How to lint

```powershell
make lint
```

Or directly:

```powershell
ruff check src/ tests/
```
## Secrets management

Copy `.env.example` to `.env` and fill in your values:
```powershell
cp .env.example .env
```

Never commit `.env` — it is listed in `.gitignore`.

## Pre-commit hooks

Install hooks after cloning:
```powershell
pip install pre-commit detect-secrets
detect-secrets scan > .secrets.baseline
pre-commit install
```

Hooks run automatically on every commit:
- `ruff` — linter and formatter
- `detect-secrets` — blocks secrets from entering the repo
