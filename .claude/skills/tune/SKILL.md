---
name: tune
description: Self-audit the Claude Code setup against recent session friction — permission prompts that should be allowlisted, repeatedly rediscovered facts that belong in rules or the runbook, guard blocks that confused rather than helped — and propose concrete diffs to settings.json, rules, CLAUDE.md, or the runbook. Use periodically ("tune the repo", "reduce friction"), or after a session that felt slow or repetitive.
---

# Tune the Claude Code setup

The repo's automation (hooks, rules, allowlist, runbook, wiki contracts) should absorb friction over time. This skill is the feedback loop: find where recent sessions lost time or tokens, turn each finding into a one-line config/rule change, apply what's approved.

## 1. Gather evidence

Look at the most recent session transcripts for THIS project (newest first, skip the current session):

```
~/.claude/projects/<this project's directory>/  — *.jsonl transcripts
```

Read them **cheaply**: Grep for friction signatures rather than reading transcripts whole. Signatures worth counting:

- **Permission prompts**: tool calls that required user approval — recurring safe read-only commands are allowlist candidates.
- **Blocked hook calls**: guard messages (commit guard, secret protection) — did the agent recover in one step? A guard that needed multiple retries needs a clearer message or a rule.
- **Rediscovery**: the same file read many times across sessions, the same error diagnosed twice (runbook candidate — hand off to `/runbook-add`), the same question answered repeatedly (rule/CLAUDE.md candidate).
- **Waste**: manual `ruff format`/`ruff check --fix` runs (hooks already do this), verbose flags (`-v`, full `git log`), full pytest runs where `--lf` or a single test would do.

Also run `node .claude/skills/openwiki/scripts/openwiki-meta.mjs impact` — chronic coverage gaps mean the wiki contracts need widening.

## 2. Propose

Produce a short table: **finding → evidence (count/example) → proposed one-line change → target file**. Targets, in order of preference:

1. `.claude/settings.json` — allowlist additions (read-only, deterministic commands only; never allowlist anything destructive), deny additions.
2. `.claude/rules/*.md` — one terse line in the right rule file; don't grow rules with prose.
3. `openwiki/troubleshooting.md` — via `/runbook-add`.
4. `CLAUDE.md` — only for things every session needs in the first screen.

Hard limits: propose at most ~5 changes per tune; each must trace to observed friction, not speculation. If nothing recurring surfaced, say the setup is tuned — that is a valid outcome.

## 3. Apply & close out

Apply the approved changes. If rules/hooks changed, the wiki's workflow page may go stale — update and re-stamp per the commit guard's instructions, and ship as a `chore(claude):` commit. Report what was tuned and what measurable friction it removes.
