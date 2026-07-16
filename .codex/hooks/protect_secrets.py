"""PreToolUse hook (Write|Edit): block writes to secret-bearing files.

`.env` (and any `.env.*` variant except `.env.example`) holds real secrets
and is gitignored; the agent must change `.env.example` and ask the developer
to mirror keys. Exit code 2 blocks the tool call with an explanation.
"""

from __future__ import annotations

import json
import re
import sys

BLOCKED = re.compile(r"(^|[\\/])\.env(\.(?!example$)[^\\/]*)?$")


def main() -> int:
    """Block Write/Edit calls that target a secret-bearing env file."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path or not BLOCKED.search(file_path):
        return 0

    print(
        f"Blocked: {file_path} holds real secrets and is not for the agent to "
        "edit. Add the key with a placeholder to .env.example instead and ask "
        "the developer to mirror the real value.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
