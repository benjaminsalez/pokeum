# Contributing to pokeum

Thanks for considering a contribution! Issues, bug reports, and pull requests are all welcome.

## Dev setup

Requirements: **Python 3.13+** and **Node 22+** (frontend only).

```bash
python -m venv .venv && . .venv/Scripts/activate   # or bin/activate on Linux
pip install -r requirements.txt -r requirements-dev.txt
pre-commit install

cd frontend && npm ci
```

For recognition work you'll want reference data: `python main.py sync --set <id>` (one set is enough for development) followed by `python main.py index build`.

## Quality gate

Every commit must pass the same checks locally that CI runs:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m compileall app main.py
python -m bandit -c pyproject.toml -r app main.py
python -m pytest
```

Frontend changes additionally need `npm run build` (vue-tsc type-check + Vite build) to pass.

`pre-commit install` wires all of this into `git commit`, plus a `detect-secrets` scan. Never commit with `--no-verify`.

## Conventions

- **Type hints are mandatory**, and every module/function/class gets a **Google-style docstring** (Ruff's `D` rules and mypy enforce this; `tests/` is exempt from docstrings — test names carry the documentation).
- **Configuration**: environment-dependent values get a named accessor in `app/core/config.py` **and** a documented key in `.env.example`. Fixed design choices are named constants in `app/core/constants.py` with a comment explaining the choice. `os.environ` is never read anywhere else.
- **Layering**: `app/core/` is the bottom layer and never imports from the rest of `app/`.
- **Logging**: `logging.getLogger(__name__)` with `%`-style lazy args — never f-strings in log calls, never `print`.
- **Tests are offline-only**: no network, no model downloads, no external services. Monkeypatch settings; use fakes behind the signal Protocols and synthetic in-process images.
- **Dependencies are pinned** (`==`). Bumping a pin means running `pip-audit` and the test suite.

## Commits & pull requests

- [Conventional Commits](https://www.conventionalcommits.org/): `feat(api): ...`, `fix(vision): ...`, `docs: ...`. Imperative mood, body explains the *why*.
- Bump `version` in `pyproject.toml` once per shippable unit: `feat` → minor, `fix`/other → patch, breaking → major.
- **Docs move with code**: the `openwiki/` pages declare which source files they cover; if your change makes a page stale, update it in the same PR. (`node .claude/skills/openwiki/scripts/openwiki-meta.mjs map <file>` tells you which page covers a file.)
- PRs target `main`. Describe what changed, why, and how you tested it — the PR template walks you through it.

## Reporting bugs

Use the bug-report issue template. For recognition misses, the card id (e.g. `sv02-025`), a description of the photo conditions, and — if you're comfortable sharing it — the photo itself make the difference between "noted" and "fixed".

## Security

Please report vulnerabilities privately — see [SECURITY.md](SECURITY.md).
