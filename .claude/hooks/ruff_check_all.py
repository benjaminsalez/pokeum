"""Stop hook: lint, format-check and type-check the whole repository.

Claude Code runs this when the agent finishes a turn. It runs ``ruff check``,
``ruff format --check`` and ``mypy`` over the project and reports any leftovers
on stderr so they surface in the conversation. It always exits 0 (advisory
only; the per-file PostToolUse hook is the one that fixes files as they are
written).

Two efficiency measures:

- **Change detection**: the full sweep only runs when the repository's Python
  state actually changed since the last clean run (HEAD + working-tree diff +
  untracked files are fingerprinted into ``.claude/hooks/.check_cache``,
  gitignored). Turns that touched no Python code cost nothing.
- **Capped output**: findings feed straight into the model's context, so each
  check reports at most ``MAX_LINES_PER_CHECK`` lines.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

CHECKS = (
    ["ruff", "check", "."],
    ["ruff", "format", "--check", "."],
)

# mypy runs through its daemon: dmypy keeps the type graph warm between turns,
# so repeat sweeps take ~100ms instead of full-graph seconds. The mypy config
# `files` list must be repeated here because dmypy takes paths on the CLI.
DMYPY_CHECK = ["mypy.dmypy", "run", "--timeout", "3600", "--", "app", "main.py", "tests"]
MYPY_FALLBACK = ["mypy"]

MAX_LINES_PER_CHECK = 25
CACHE_FILE = Path(__file__).parent / ".check_cache"


def run(args: list[str]) -> tuple[int, str]:
    """Run a command, returning (returncode, combined output)."""
    result = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    return result.returncode, ((result.stdout or "") + (result.stderr or "")).strip()


def run_mypy() -> tuple[int, str]:
    """Type-check via the dmypy daemon, falling back to plain mypy.

    Returns:
        (returncode, output) of whichever runner produced a verdict.
    """
    code, output = run([sys.executable, "-m", *DMYPY_CHECK])
    # dmypy exits 2 on daemon/crash problems and prints no findings; a broken
    # daemon must not masquerade as a type error, so restart once, then fall
    # back to the plain (slow but reliable) runner.
    if code != 0 and "error:" not in output:
        run([sys.executable, "-m", "mypy.dmypy", "kill"])
        code, output = run([sys.executable, "-m", *DMYPY_CHECK])
        if code != 0 and "error:" not in output:
            return run([sys.executable, "-m", *MYPY_FALLBACK])
    return code, output


def python_state_fingerprint() -> str | None:
    """Fingerprint of everything that could change a check result, or None.

    Combines HEAD, the working-tree diff, and the content of untracked Python
    files. ``None`` (git unavailable) disables the fast path so checks always
    run.
    """
    code, head = run(["git", "rev-parse", "HEAD"])
    if code != 0:
        return None
    digest = hashlib.sha256(head.encode())
    _, diff = run(["git", "diff", "HEAD"])
    digest.update(diff.encode())
    _, untracked = run(["git", "ls-files", "--others", "--exclude-standard", "*.py"])
    for name in untracked.splitlines():
        path = Path(name.strip())
        digest.update(name.encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            pass
    return digest.hexdigest()


def main() -> int:
    """Run repo-wide checks unless nothing changed; report capped findings.

    Returns:
        Always 0, so a finding never aborts the session.
    """
    fingerprint = python_state_fingerprint()
    if fingerprint is not None:
        try:
            if CACHE_FILE.read_text(encoding="utf-8").strip() == fingerprint:
                return 0  # clean run already recorded for this exact state
        except OSError:
            pass

    clean = True
    results = [(args, *run([sys.executable, "-m", *args])) for args in CHECKS]
    results.append((MYPY_FALLBACK, *run_mypy()))
    for args, code, output in results:
        if code != 0:
            clean = False
            lines = output.splitlines()
            if len(lines) > MAX_LINES_PER_CHECK:
                lines = lines[:MAX_LINES_PER_CHECK] + [
                    f"... {len(lines) - MAX_LINES_PER_CHECK} more lines - "
                    f"run `python -m {' '.join(args)}` for the full list."
                ]
            print("\n".join(lines), file=sys.stderr)

    if clean and fingerprint is not None:
        try:
            CACHE_FILE.write_text(fingerprint, encoding="utf-8")
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
