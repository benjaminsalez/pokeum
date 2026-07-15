---
title: Troubleshooting runbook
sources: ["app/core/**", "main.py", ".env.example"]
read-when: "a command failed with an error you recognize from this repo, or you are adding a newly diagnosed failure mode"
verified: 26c9ccc9aeb1
---

# Troubleshooting runbook

Known failure signatures in this repo and their one-line fixes. This page
is **machine-read**: the `runbook_matcher` hook scans every failed command's
output against the `pattern:` lines below and injects the matching `fix:` into
the agent's context automatically. Keep entries in exactly this two-line shape
(a `pattern:` line with a backtick-wrapped regex, immediately followed by a
`fix:` line) or the hook will not pick them up.

When you diagnose a new repo-specific failure that took real effort to
understand, add an entry — that is how the runbook earns its keep. Keep
patterns specific enough not to false-positive on unrelated output, and keep
fixes to one actionable line. Delete entries whose failure mode no longer
exists.

## Missing configuration

pattern: `MissingSettingError`
fix: A required setting is unset - add the key to your environment/.env (see .env.example for the full key list and app/core/config.py for which accessor requires it).

## Environment not installed

pattern: `No module named (ruff|mypy|pytest|bandit|pre_commit|pip_audit)`
fix: Dev tooling is not installed in this environment - activate the venv and run `pip install -r requirements-dev.txt`.

pattern: `No module named 'app'`
fix: Run from the repository root (imports are rooted there), not from a subdirectory.

## Recognizer

pattern: `cannot (read image|decode image bytes)`
fix: OpenCV could not load the input - check the path exists and points to a real image file (identify), or that the uploaded bytes are a valid image (API /identify returns 400 for this).

## Quality gate

pattern: `would be reformatted`
fix: Do not run the formatter manually - the PostToolUse hook formats files on write; if this came from the gate, a non-Claude edit slipped in: run `python -m ruff format .` once.

pattern: `error: Cannot find implementation or library stub`
fix: mypy cannot resolve an import - if it is a third-party package without stubs this is normally silenced by ignore_missing_imports in pyproject.toml; check the module name for a typo first.

## Git & hooks

pattern: `husky|pre-commit hook.*failed|hook script failed`
fix: The commit gate found real findings - read the failing check's output above and fix the findings; never retry with --no-verify (it is blocked anyway).

pattern: `detect-secrets.*Potential secrets`
fix: detect-secrets flagged staged content - if it is a real secret, remove it; if it is a false positive (e.g. a hash), add a `pragma: allowlist secret` comment or extend the exclude, never commit a real credential.
