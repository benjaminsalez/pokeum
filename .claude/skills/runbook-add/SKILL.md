---
name: runbook-add
description: Add a newly diagnosed failure mode to the troubleshooting runbook (openwiki/troubleshooting.md) in the exact machine-readable pattern/fix format the runbook_matcher hook consumes, then verify the pattern actually matches. Use after diagnosing a repo-specific error that took real effort to understand, or when the user says "add this to the runbook".
argument-hint: "[short description of the failure]"
---

# Add a runbook entry

The runbook turns a one-time diagnosis into permanent, automatic knowledge: the `runbook_matcher` hook injects the `fix:` line the moment a future command fails with the matching `pattern:`. A badly-formatted or over-broad entry is worse than none — follow this exactly.

## 1. Qualify

Only add entries that earn their keep:

- **Repo-specific** — generic Python errors an agent already understands don't belong here.
- **Recurring-by-nature** — misconfiguration, environment drift, tooling quirks. Not one-off typos.
- **Cost real diagnosis effort** — if the error message already says the fix, skip it.

Check the existing entries in `openwiki/troubleshooting.md` first — extend/sharpen an existing pattern rather than adding a near-duplicate.

## 2. Write the entry

Append under the fitting `##` section (or add one) in **exactly** this two-line shape:

```
pattern: `<regex that matches the failure output>`
fix: <one actionable line: what this means and what to do>
```

- The pattern is a Python regex searched against the failed command's full output. Make it **specific enough to never false-positive** on healthy output (match the exception name or a distinctive phrase, not a common word).
- The fix is ONE line, imperative, with the concrete file/command to act on.

## 3. Verify it fires

Test the pattern against a simulated payload before finishing:

```bash
echo '{"session_id":"test-runbook","tool_response":{"stdout":"<paste a representative slice of the real error output>"}}' | python .claude/hooks/runbook_matcher.py
```

Expect the JSON injection containing your fix line. Also confirm healthy output does NOT match. (The test session id keeps dedupe state out of your real session.)

## 4. Close out

Run the wiki lint, re-stamp the page, and commit (a `docs:` commit — no version bump needed):

```bash
node .claude/skills/openwiki/scripts/openwiki-meta.mjs lint
node .claude/skills/openwiki/scripts/openwiki-meta.mjs record update --pages troubleshooting.md
```

Report: the entry added, the section it lives in, and the verification result.
