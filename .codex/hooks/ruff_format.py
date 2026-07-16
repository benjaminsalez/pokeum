"""PostToolUse hook: lint and format Python files with Ruff after each write.

Claude Code runs this after every Write/Edit. It reads the hook payload from
stdin and, when the touched file is a ``.py`` file, runs ``ruff check --fix``
followed by ``ruff format`` on it. Problems are reported on stderr but never
block the edit (the script always exits 0).
"""

from __future__ import annotations

import json
import subprocess
import sys


def main() -> int:
    """Lint and format the file named in the hook payload; never block the edit.

    Returns:
        Always 0, so a Ruff finding never aborts the write.
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path.endswith(".py"):
        return 0

    for args in (["check", "--fix", file_path], ["format", file_path]):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 and result.stderr:
            print(result.stderr, file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
