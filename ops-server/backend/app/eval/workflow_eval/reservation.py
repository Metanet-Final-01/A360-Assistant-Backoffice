"""Shared reservation guard for live workflow evaluations."""

from datetime import datetime, timezone
from threading import Lock

_lock = Lock()
_active = False


def reserve_state(state: dict, values: dict) -> bool:
    global _active
    with _lock:
        if _active or state.get("running"):
            return False
        _active = True
        state.update(values)
        return True


def finish_state(state: dict) -> None:
    global _active
    with _lock:
        state.update({"running": False, "finished_at": datetime.now(timezone.utc).isoformat()})
        _active = False
