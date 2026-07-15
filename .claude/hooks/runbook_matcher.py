"""PostToolUse hook (Bash): match command failures against the wiki runbook.

When a shell command's output contains a known failure signature, this hook
injects the matching one-line fix from ``openwiki/troubleshooting.md`` —
deterministic institutional memory, delivered at the exact moment the error
happens instead of waiting for the agent to rediscover the cause.

Runbook entry format (in the page body, one pair per known error)::

    pattern: `regex or literal to search for`
    fix: one line telling the agent what this means and what to do

Each pattern fires at most once per session (``hook_state``).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import hook_state

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "openwiki" / "troubleshooting.md"
ENTRY = re.compile(r"^pattern:\s*`(?P<pattern>.+)`\s*\n^fix:\s*(?P<fix>.+)$", re.MULTILINE)
MAX_HINTS = 2


def load_runbook() -> list[tuple[str, str]]:
    """Parse (pattern, fix) pairs from the troubleshooting page."""
    try:
        text = RUNBOOK.read_text(encoding="utf-8")
    except OSError:
        return []
    return [(m.group("pattern"), m.group("fix").strip()) for m in ENTRY.finditer(text)]


def main() -> int:
    """Scan Bash output for known failure signatures; inject matching fixes."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    # tool_response shape varies across versions; scan its JSON form wholesale.
    haystack = json.dumps(payload.get("tool_response", ""))
    if not haystack or haystack == '""':
        return 0

    session_id = str(payload.get("session_id", ""))
    hints = []
    for pattern, fix in load_runbook():
        try:
            if not re.search(pattern, haystack):
                continue
        except re.error:
            continue
        if hook_state.seen_before("runbook", session_id, pattern):
            continue
        hints.append(f"- {fix}")
        if len(hints) >= MAX_HINTS:
            break

    if not hints:
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        "[runbook match - openwiki/troubleshooting.md]\n" + "\n".join(hints)
                    ),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
