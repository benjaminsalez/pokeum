# Agent workflow (always applies)

How to work in this repo efficiently. The repo's automation already covers a lot — don't duplicate what it does.

## Orient before you edit

- The SessionStart brief already told you the branch, dirty files, and wiki freshness — don't re-derive them.
- Before modifying source files, look up their docs: `node .claude/skills/openwiki/scripts/openwiki-meta.mjs map <file...>`. Read only the pages it names; don't crawl the wiki. A page marked STALE means trust source over doc.
- Read targeted (specific files/line ranges, Grep for symbols) rather than whole directories. Delegate broad multi-file exploration to a read-only subagent and keep only its conclusions.

## Let the hooks work

- **Never run `ruff format` or `ruff check --fix` manually** — the PostToolUse hook formats every file you write, and the Stop hook reports repo-wide leftovers. Only act on what the hooks report.
- `.env` and its variants are blocked for you (hook + permission deny). Change `.env.example` and ask the developer to mirror keys.
- `git commit` is guarded three ways: `--no-verify` is always blocked; a commit is refused while the OpenWiki is stale for the changes being committed (updating the stale pages and staging them **with** the code satisfies it — re-stamp via `record update --pages` after, in a metadata-only commit); and a code-bearing commit without a `pyproject.toml` semver bump is refused for bump-carrying commit types. Escape hatches, each with a stated reason: `OPENWIKI_SKIP=1`, `VERSION_OK=1`. `/ship` walks all of this.

## Keep output lean

- Tooling is configured for terse output (`pytest -q -ra`, concise ruff) — don't add `-v`/`--verbose` unless actually diagnosing, and drop it afterwards.
- When a command can answer with less (e.g. `git log --oneline -5` vs full log, `pytest tests/test_x.py::test_y` vs the whole suite), prefer less.
- Debug loop: after a failing test run, iterate with `python -m pytest --lf` (last failures only) until green, then one full `python -m pytest` to confirm.
- Don't paste long tool output back into the conversation; summarize and reference.
- Temp scripts, probe files, and one-off outputs go in gitignored `.claude/scratch/` — never the repo root. A clean `git status` keeps the session brief, staging, and the commit guard accurate.

## Definition of done

- Behaviour changed → the matching wiki page is updated in the same piece of work (the commit guard enforces this).
- The verify gate passes: `python -m ruff check . && python -m ruff format --check . && python -m mypy && python -m pytest` (pre-commit and CI run the same set — "passed locally" must mean "will pass in CI").
- New settings followed the checklist in `config.md`; new behaviour has offline tests per `tests.md`.
- Commit via the `/ship` skill conventions: Conventional Commits, heredoc message, semver bump in `pyproject.toml` once per shippable unit.
