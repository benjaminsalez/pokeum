"""SessionStart hook: inject a tiny repo brief into the session context.

Claude Code runs this once when a session starts; stdout is added to the
model's context. The brief must stay small (~10 lines) — it is a context
*investment* that saves the model its usual first rounds of discovery
(branch? dirty files? docs current?), not a status report for humans.
"""

from __future__ import annotations

import subprocess
import sys

MAX_DIRTY_LINES = 5


def run(args: list[str], timeout: float = 10.0) -> tuple[int, str]:
    """Run a command and return (returncode, combined trimmed output)."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return result.returncode, ((result.stdout or "") + (result.stderr or "")).strip()


def git_brief() -> list[str]:
    """Branch, upstream delta, and a capped dirty-file list."""
    lines: list[str] = []
    _, branch = run(["git", "branch", "--show-current"])
    _, describe = run(["git", "log", "-1", "--format=%h %s"])
    if branch:
        upstream = ""
        code, counts = run(
            ["git", "rev-list", "--left-right", "--count", f"{branch}...@{{upstream}}"]
        )
        if code == 0 and counts:
            ahead, _, behind = counts.partition("\t")
            if ahead.strip() not in ("", "0") or behind.strip() not in ("", "0"):
                upstream = f" (ahead {ahead.strip()}, behind {behind.strip()})"
        lines.append(f"branch: {branch}{upstream} @ {describe}")
    _, status = run(["git", "status", "--porcelain"])
    if status:
        dirty = status.splitlines()
        shown = ", ".join(entry.strip() for entry in dirty[:MAX_DIRTY_LINES])
        more = f" (+{len(dirty) - MAX_DIRTY_LINES} more)" if len(dirty) > MAX_DIRTY_LINES else ""
        lines.append(f"dirty: {len(dirty)} file(s): {shown}{more}")
    else:
        lines.append("dirty: clean tree")
    return lines


def wiki_brief() -> str:
    """One line of OpenWiki freshness, degrading gracefully without node."""
    code, out = run(
        ["node", ".claude/skills/openwiki/scripts/openwiki-meta.mjs", "impact"],
        timeout=15.0,
    )
    if not out:
        return "openwiki: status unknown (node unavailable?)"
    stale = [line for line in out.splitlines() if line.startswith("- openwiki/")]
    if "STALE PAGES (edit" in out:
        pages = ", ".join(line.split()[1] for line in stale[:4])
        return f"openwiki: STALE - {pages} (run /openwiki before relying on docs)"
    if "COVERAGE GAPS" in out:
        return "openwiki: current, but recent changes are not covered by any page"
    return (
        "openwiki: current - use `node .claude/skills/openwiki/scripts/"
        "openwiki-meta.mjs map <file...>` before editing source"
    )


def main() -> int:
    """Print the session brief; never block session start."""
    lines = ["[repo brief]"]
    lines += git_brief()
    lines.append(wiki_brief())
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
