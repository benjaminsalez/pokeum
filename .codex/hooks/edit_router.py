"""PostToolUse hook (Read|Edit|Write): route wiki context for touched files.

Complements ``wiki_router.py`` (which routes files the *user* mentions): when
the agent itself navigates to a source file the user never named, this hook
injects the OpenWiki ``map`` routing for it — which page covers the file, and
whether that page is fresh or stale.

Per-session dedupe (``hook_state``): each file is routed at most once per
session, so repeated reads/edits of the same file cost nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import hook_state

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTABLE_SUFFIXES = {".py", ".toml", ".txt", ".yml", ".yaml", ".sh", ".ps1"}
MAX_OUTPUT_LINES = 8


def repo_relative(file_path: str) -> str | None:
    """Normalize a tool-input path to repo-relative posix, or None if outside."""
    try:
        path = Path(file_path)
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except (ValueError, OSError):
        return None


def main() -> int:
    """Inject wiki routing for the touched file, once per file per session."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    rel = repo_relative(file_path) if file_path else None
    if (
        rel is None
        or Path(rel).suffix not in ROUTABLE_SUFFIXES
        or rel.startswith(("openwiki/", ".claude/"))
        or not (REPO_ROOT / rel).is_file()
    ):
        return 0

    session_id = str(payload.get("session_id", ""))
    if hook_state.seen_before("routed", session_id, rel):
        return 0

    try:
        result = subprocess.run(
            ["node", ".claude/skills/openwiki/scripts/openwiki-meta.mjs", "map", rel],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            cwd=REPO_ROOT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0

    output = (result.stdout or "").strip()
    if "read:" not in output:
        return 0  # uncovered file: nothing useful to inject

    lines = output.splitlines()
    lines = [line for line in lines if not line.startswith("Entrypoint")][:MAX_OUTPUT_LINES]
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": "[openwiki routing]\n" + "\n".join(lines).strip(),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
