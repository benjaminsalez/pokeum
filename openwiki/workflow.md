---
title: Development workflow
sources: ["app/core/config.py", "app/core/logging_config.py", "tests/**", ".env.example", ".claude/rules/**", ".claude/hooks/**", ".claude/settings.json", ".codex/**", ".agents/**", "AGENTS.md", ".pre-commit-config.yaml", "pyproject.toml", "requirements.txt", "requirements-dev.txt", "scripts/fresh-start.sh", "scripts/fresh-start.ps1", "scripts/claude-headroom.sh", "scripts/claude-headroom.ps1"]
read-when: "changing settings/logging, tests, dependencies, quality-gate config, CI, hooks/automation, coding conventions, or the fresh-start/detach script"
verified: 1b25d297d79f
---

# Development workflow

How this repo keeps the codebase consistent: the settings/constants split, the
conventions that are enforced (not just suggested), and the gate that checks all
of it locally and in CI. These conventions predate the recognizer — they came
from the vva-python-template skeleton pokeum grew out of — and still govern every
change. The domain code they apply to is documented in
[recognition-pipeline](recognition-pipeline.md),
[reference-data](reference-data.md), and [service-and-cli](service-and-cli.md).

## Settings vs. constants

Every configurable value is one of exactly two kinds, and each has exactly one
home (`.claude/rules/config.md`):

- **Varies per environment** (endpoints, keys, log level) → a named accessor in
  [`app/core/config.py`](../app/core/config.py). `config.require("NAME")` for
  values that are security- or correctness-critical and must fail fast
  (`MissingSettingError`) rather than run with a silently empty value;
  `config.get("NAME", default)` otherwise. `os.environ` is never read outside
  `config.py` — every call site goes through a named accessor.
- **Fixed design decision** (a limit, a batch size, a schedule) → a named
  constant in [`app/core/constants.py`](../app/core/constants.py), grouped by
  domain, with a comment explaining the choice. Changed only through a reviewed
  code change, never by an environment variable.

Adding a new setting is a two-place checklist, every time: the accessor in
`config.py`, and a key with a placeholder value in `.env.example`. The
`/new-setting` skill (`.claude/skills/new-setting/SKILL.md`) walks this exact
checklist and classifies a new value into the right bucket first.

`python main.py ...` loads a local `.env` automatically at startup
(python-dotenv in `main.py`); variables already set in the shell or platform
always win, so deployments configured via the environment behave unchanged.

## Coding conventions (`.claude/rules/conventions.md`)

- English throughout: code, comments, docstrings, log messages.
- Type hints are mandatory; Google-style docstrings are required on every
  module, function, and class (enforced by Ruff's `D` rules and mypy — `tests/`
  is exempt from the docstring requirement since test names carry the
  documentation).
- Logging goes through `logger = logging.getLogger(__name__)` with `%`-style
  lazy arguments — never f-strings in a log call, never `print`. `INFO` marks
  milestones, `DEBUG` is everything the code does.
  [`app/core/logging_config.py`](../app/core/logging_config.py) is the single
  place that configures format/level/routing: one stderr handler, JSON output
  when `LOG_JSON=true`, third-party library logging pinned to `WARNING` even in
  debug so "debug" means *our* code.
- Comments explain *why*, not *what*.
- `app/core/` cannot import from the rest of `app/` (it's the bottom layer).
- Dependencies in `requirements.txt` / `requirements-dev.txt` are pinned
  (`==`); bumping a pin means running `pip_audit` and the test suite before
  committing.

## Tests (`.claude/rules/tests.md`)

The suite under `tests/` is **offline-only** — no network, no external
services. Anything that needs a running service doesn't belong there.
Environment-dependent code (like `app/core/config.py`) is tested by
monkeypatching settings (`monkeypatch.setenv`/`delenv`), never by reading a
developer's real environment — see
[`tests/test_config.py`](../tests/test_config.py) for the pattern. The
recognizer's tests keep the same discipline: signals sit behind Protocols so
pure logic (`tests/test_fusion.py`, `tests/test_temporal.py`,
`tests/test_ocr_parse.py`) is tested with fakes, sync is tested with
`httpx.MockTransport` (`tests/test_sync.py`), and image tests build synthetic
images in-process — no network, no model downloads. There is no fixed coverage
target: cover logic where mistakes hurt, skip trivial pass-throughs.

## The quality gate

The same six checks run locally via pre-commit or the `/verify` skill (the
Bitbucket CI pipeline that used to mirror them was removed with the move to
GitHub hosting; re-add a CI workflow running this exact list when CI returns):

```
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m compileall app main.py
python -m bandit -c pyproject.toml -r app main.py
python -m pytest
```

Pre-commit additionally runs `detect-secrets-hook` (baseline in
`.secrets.baseline`); `pip_audit` runs on dependency bumps. Tool versions are
pinned in `requirements-dev.txt` so every environment uses identical versions.

The Vue frontend (`frontend/`) has its own separate check — `npm run build`
(vue-tsc type-check + Vite build) — which is not part of the Python gate; run
it when touching `frontend/`. See [service-and-cli](service-and-cli.md).

- **Pre-commit** (`.pre-commit-config.yaml`): one-time setup per clone is
  `pip install -r requirements-dev.txt && pre-commit install`. All hooks are
  `language: system`, so they run the same pinned tools instead of pre-commit
  managing its own environments.
- **`/verify` skill**: runs the full gate, auto-fixes mechanical findings
  (`ruff check --fix`, `ruff format`), and reports one pass/fail summary.
- **`/ship` skill**: the commit → push → PR flow. Conventional Commit messages
  via heredoc, a semver bump in `pyproject.toml` per shippable change (`feat`
  → minor, `fix`/other → patch, `BREAKING CHANGE` → major), and Dutch-language
  PR bodies with a fixed section structure. `dev` is the trunk — there is no
  `main`; solo projects may commit/push directly to `dev`, team projects use
  `<type>/<short-slug>` branches into `dev`.

## Agent automation (`.claude/`)

The repo enforces its own discipline on Claude Code sessions via hooks wired
in [`.claude/settings.json`](../.claude/settings.json):

- **SessionStart** (`session_start.py`) injects a compact repo brief — branch,
  dirty files, OpenWiki freshness — so a session starts oriented.
- **UserPromptSubmit** (`wiki_router.py`) detects repository file paths in the
  user's prompt and injects the OpenWiki `map` routing for them automatically;
  `edit_router.py` does the same when the agent itself reads or edits a file
  the user never named. Both share a per-session dedupe
  (`hook_state.py`, state in `.claude/hooks/.state/`, gitignored), so each
  file is routed at most once per session.
- **PreToolUse** guards (`guard_commit.py`): `git commit --no-verify` is
  blocked outright; commits are blocked while the wiki is stale for the
  committed changes unless the updated pages are staged along (escape hatch:
  `OPENWIKI_SKIP=1` with a stated reason); code-bearing commits without a
  semver bump in `pyproject.toml` are blocked for bump-carrying commit types
  (escape hatch: `VERSION_OK=1`). `protect_secrets.py` blocks writes to
  `.env` and variants.
- **PostToolUse** (`ruff_format.py`) lints+formats every written Python file —
  agents must never run formatters manually. `runbook_matcher.py` scans
  failed command output against [troubleshooting.md](troubleshooting.md) and
  injects the matching one-line fix (once per pattern per session).
- **Stop** (`ruff_check_all.py`) sweeps the repo with Ruff + mypy, but only
  when the Python state actually changed since the last clean run
  (fingerprint cache in `.claude/hooks/.check_cache`, gitignored), and caps
  its output at 25 lines per check. mypy runs through its daemon (`dmypy`,
  state in gitignored `.dmypy.json`) so warm sweeps take ~100ms, with an
  automatic restart-then-fallback to plain mypy.

The same discipline is mirrored for OpenAI Codex sessions: `.codex/` carries
the harness config and equivalent hooks, `.agents/skills/` the equivalent
skills, and `AGENTS.md` the Codex-facing onboarding doc (the analog of
`CLAUDE.md`). The `.claude/` versions are canonical — change those first and
mirror deliberately; `.codex/hooks/.state/` and `.check_cache` are per-clone
state and gitignored like their Claude counterparts.

The automation layer maintains itself through two skills: `/runbook-add`
turns a newly diagnosed failure into a machine-matched runbook entry, and
`/tune` audits recent session transcripts for friction (permission prompts,
rediscovery, waste) and proposes allowlist/rule/runbook changes.

Tool output is deliberately terse everywhere (pytest `-q -ra --no-header`,
Ruff `output-format = "concise"`, `NO_COLOR` in the session env): everything a
tool prints is context the agent pays for. The optional
[`scripts/claude-headroom.ps1`](../scripts/claude-headroom.ps1)/`.sh` wrappers
start Claude Code behind the Headroom compression proxy (pinned in
`requirements-dev.txt`) for a further token cut on log-heavy sessions.

## Detaching from the template

[`scripts/fresh-start.sh`](../scripts/fresh-start.sh) (and the PowerShell
equivalent `fresh-start.ps1`) removes the cloned `.git` directory — dropping
the template's history and remote — and re-initializes the repo on branch
`dev` with a single "Initial commit". The working tree is left untouched. Run
it once, right after cloning, before starting real work.
