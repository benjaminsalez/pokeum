"""Shared per-session state for hooks (gitignored, best-effort).

Hooks that inject context must not repeat themselves within a session — a
second identical injection is pure token waste. State lives in one JSON file
per concern under ``.claude/hooks/.state/``, keyed by the Claude Code
``session_id`` from the hook payload: a new session resets the slate.
"""

from __future__ import annotations

import json
from pathlib import Path

STATE_DIR = Path(__file__).parent / ".state"


def seen_before(concern: str, session_id: str, key: str) -> bool:
    """Return whether *key* was already recorded this session; record it.

    Best-effort: any I/O failure reports "not seen" so the hook still works,
    it just loses dedupe.

    Args:
        concern: State-file name, one per hook (e.g. ``"routed"``).
        session_id: The session id from the hook payload.
        key: The item to dedupe on (a file path, a pattern name, ...).

    Returns:
        True when the key was already recorded for this session.
    """
    state_file = STATE_DIR / f"{concern}.json"
    state: dict[str, object] = {}
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        state = {}
    if state.get("session_id") != session_id:
        state = {"session_id": session_id, "keys": []}
    keys = state.get("keys")
    if not isinstance(keys, list):
        keys = []
        state["keys"] = keys
    if key in keys:
        return True
    keys.append(key)
    try:
        STATE_DIR.mkdir(exist_ok=True)
        state_file.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass
    return False
