"""Shared reservation guard for BFCL evaluation jobs."""

from datetime import datetime, timezone
from threading import Lock

_lock = Lock()
_active = False


def reserve_state(state: dict, values: dict) -> bool:
    """Atomically reserve the BFCL evaluator across single-run and pass@k modes."""
    global _active
    with _lock:
        if _active or state.get("running"):
            return False
        _active = True
        state.update(values)
        return True


def finish_state(state: dict) -> None:
    """Release the shared BFCL reservation and mark the owning state as finished."""
    global _active
    with _lock:
        state.update({"running": False, "finished_at": datetime.now(timezone.utc).isoformat()})
        _active = False
