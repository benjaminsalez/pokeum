"""UserPromptSubmit hook: auto-route OpenWiki context for mentioned files.

When the user's prompt mentions repository file paths, this hook runs the
OpenWiki ``map`` lookup for them and injects the result (which pages cover
those files, with freshness flags) into context. The agent gets its docs
routing for free, without having to remember to run ``map`` itself.

Deliberately conservative: silent unless the prompt names at least one real,
wiki-relevant file; output capped so it stays a context investment, not a tax.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import hook_state

# Path-shaped tokens: at least one separator, a known code/config extension.
PATH_PATTERN = re.compile(r"\b[\w.-]+(?:[\\/][\w.-]+)+\.(?:py|toml|txt|yml|yaml|json|md|sh|ps1)\b")
MAX_FILES = 4
MAX_OUTPUT_LINES = 14


def main() -> int:
    """Inject wiki routing for file paths mentioned in the prompt."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    prompt = payload.get("prompt", "")
    if not prompt:
        return 0

    session_id = str(payload.get("session_id", ""))
    mentioned = []
    for token in PATH_PATTERN.findall(prompt):
        normalized = token.replace("\\", "/")
        if normalized.startswith("openwiki/") or normalized.startswith(".claude/"):
            continue  # wiki/tooling files need no routing
        if not Path(normalized).is_file() or normalized in mentioned:
            continue
        # Shared dedupe with edit_router: a file routed once this session
        # (from either direction) is never routed again.
        if hook_state.seen_before("routed", session_id, normalized):
            continue
        mentioned.append(normalized)
    if not mentioned:
        return 0

    try:
        map_cmd = ["node", ".claude/skills/openwiki/scripts/openwiki-meta.mjs", "map"]
        result = subprocess.run(
            [*map_cmd, *mentioned[:MAX_FILES]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0

    output = (result.stdout or "").strip()
    if not output or "(no wiki page covers" in output and "read:" not in output:
        return 0  # nothing useful to add

    lines = output.splitlines()[:MAX_OUTPUT_LINES]
    print("[openwiki routing for files mentioned in the prompt]")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
