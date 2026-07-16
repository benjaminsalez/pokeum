"""PreToolUse hook (Bash): guard `git commit` invocations.

Two guards, both deterministic:

1. **Never bypass hooks** — a commit with `--no-verify`/`-n` is blocked
   outright; the quality gate is not optional.
2. **Docs travel with code** — before a commit is allowed, the OpenWiki
   impact analysis must report no stale pages. If source files covered by a
   wiki page changed since that page was last verified, the commit is blocked
   until the wiki is updated (`/openwiki`) or the author explicitly opts out
   for this one commit by prefixing it with `OPENWIKI_SKIP=1`.

Exit code 2 blocks the tool call; stderr is fed back to the model so it can
self-correct. Everything else (exit 0) lets the command through — including
when node is unavailable: the guard fails open, the CI gate still catches it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

IMPACT_CMD = ["node", ".claude/skills/openwiki/scripts/openwiki-meta.mjs", "impact"]


def is_commit(command: str) -> bool:
    """True when the shell command performs a git commit."""
    return re.search(r"\bgit\b[^|;&]*\bcommit\b", command) is not None


def run_git(args: list[str]) -> str:
    """Run a git command, returning its stdout ('' on any failure)."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout or ""


# Commit types that never require a semver bump (per the /ship skill, the bump
# rides in feature commits: feat -> minor, fix -> patch, BREAKING -> major).
BUMP_EXEMPT_TYPES = re.compile(r"\b(docs|chore|ci|test|build|style)(\(|!|:)")


def needs_version_bump(command: str, staged: set[str]) -> bool:
    """True when a code-bearing commit is staged without a version bump."""
    if "--amend" in command or "VERSION_OK=1" in command or BUMP_EXEMPT_TYPES.search(command):
        return False
    touches_code = any(name == "main.py" or name.startswith("app/") for name in staged)
    if not touches_code:
        return False
    version_bumped = re.search(
        r"^\+version\s*=", run_git(["diff", "--cached", "-U0", "--", "pyproject.toml"]), re.M
    )
    return not version_bumped


def main() -> int:
    """Inspect the pending Bash command; block bad commits, allow the rest."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    if not command or not is_commit(command):
        return 0

    if re.search(r"(--no-verify\b|\s-n\b)", command):
        print(
            "Blocked: `git commit --no-verify` skips the quality gate. "
            "Fix what the hooks report instead of bypassing them.",
            file=sys.stderr,
        )
        return 2

    staged = {name.strip() for name in run_git(["diff", "--cached", "--name-only"]).splitlines()}
    if needs_version_bump(command, staged):
        print(
            "Blocked: this commit changes app code but pyproject.toml's "
            "version is not bumped. Bump semver once per shippable unit "
            "(feat -> minor, fix -> patch, BREAKING CHANGE -> major) and "
            "stage it. If the bump belongs to another commit in this unit, "
            "prefix the command with VERSION_OK=1.",
            file=sys.stderr,
        )
        return 2

    if "OPENWIKI_SKIP=1" in command:
        return 0

    try:
        result = subprocess.run(
            IMPACT_CMD,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0  # fail open: no node, no verdict — CI still gates

    output = result.stdout or ""
    if "STALE PAGES (edit" not in output:
        return 0

    stale_pages = [
        line[2:].split()[0] for line in output.splitlines() if line.startswith("- openwiki/")
    ]

    # Docs riding along in the same commit are fine: when every stale page is
    # itself staged, the wiki update ships with the code that staled it.
    # (Re-stamp with `record update --pages` AFTER committing; the metadata
    # commit only touches openwiki/ and can never be stale.)
    if stale_pages and all(page in staged for page in stale_pages):
        return 0

    print(
        "Blocked: the OpenWiki is stale for changes in this commit - "
        f"pages: {', '.join(stale_pages) or '(see impact output)'}.\n"
        "Update the stale pages and stage them with the code (or run "
        "/openwiki); after committing, re-stamp with `node "
        ".claude/skills/openwiki/scripts/openwiki-meta.mjs record update "
        "--pages <pages>` and commit the metadata.\n"
        "Deliberately committing without a docs update: prefix the command "
        "with OPENWIKI_SKIP=1 and say why in the conversation.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
