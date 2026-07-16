# pokeum

Project rules live in `.claude/rules/` (workflow, conventions, config, tests) and are always in effect — they are agent-agnostic and apply to Codex sessions too. Codex-specific skills live in `.agents/skills/`; the Codex harness config and hooks live in `.codex/`.

## Guardrails & automation

The repo automates its own discipline — work with it, not around it:

- **SessionStart** injects a repo brief (branch, dirty files, wiki freshness) — trust it instead of re-deriving.
- **On every Write/Edit** the touched Python file is auto-formatted (Ruff); a **Stop** check reports repo-wide lint/type leftovers (capped output). Never run formatters manually.
- **`.env` and variants are blocked** (hook + permission deny); change `.env.example` instead.
- **`git commit` is guarded**: `--no-verify` is always refused, and commits are blocked while the OpenWiki is stale for the changes being committed — update the wiki first, or prefix with `OPENWIKI_SKIP=1` for a deliberate, explained exception.
- **Optional token saver**: start sessions through `scripts/claude-headroom.ps1`/`.sh` to run behind the Headroom compression proxy (installed via `requirements-dev.txt`; running the agent CLI directly always works too).

## OpenWiki

This repository has documentation located in the /openwiki directory, built for both humans and coding agents.

Start here:
- [OpenWiki quickstart](openwiki/quickstart.md)

Targeted lookup — before modifying source files, find the relevant docs for exactly those files:

    node .agents/skills/openwiki/scripts/openwiki-meta.mjs map <file...>

(If that script is not present in this checkout, fall back to reading the quickstart and following its links. Each wiki page's frontmatter lists the source globs it covers and a read-when hint.)

Pages marked STALE by the tool have had their source files change since last verification — trust current source over the doc, and consider running an OpenWiki update.

### Maintaining the wiki

The wiki is generated and maintained by the openwiki skills in `.agents/skills/`:

- `/openwiki` — **autopilot, the only command to remember**: assesses the wiki's state and does everything needed — initializes if no wiki exists, updates stale pages after code changes, and folds in a quality/prune pass when health signals call for it. A "wiki is current" no-op result is normal.
- `/openwiki-init`, `/openwiki-update`, `/openwiki-review` — force exactly one mode, for when you want just that.

Do not hand-edit `openwiki/.last-update.json` or the `verified:` frontmatter lines — the toolkit stamps those. Substantive wiki edits outside these skills are fine, but keep each page's `sources:` globs accurate.
