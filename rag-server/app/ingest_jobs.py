"""Durable-ish RAG ingest job runner.

The scheduler/front-end only needs a stable HTTP contract, while this module
owns process-level details: cross-process reservation, run state, and log files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .rag import config

REPO_ROOT = Path(__file__).resolve().parents[1]
OPTION_SCRIPTS = {
    1: REPO_ROOT / "app" / "rag" / "scripts" / "run_option1_jar_only.py",
    2: REPO_ROOT / "app" / "rag" / "scripts" / "run_option2_with_naive_actions.py",
    3: REPO_ROOT / "app" / "rag" / "scripts" / "run_option3_with_doc_agent.py",
}

_MAX_STATUS_LOG_CHARS = 12000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    config.INGEST_RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _pid_is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock() -> dict[str, Any]:
    return _read_json(config.INGEST_JOB_LOCK_DIR / "owner.json")


def _write_lock(run_id: str, pid: int | None) -> None:
    _write_json_atomic(
        config.INGEST_JOB_LOCK_DIR / "owner.json",
        {"run_id": run_id, "pid": pid, "updated_at": _now()},
    )


def _remove_lock() -> None:
    if _lock_exists():
        shutil.rmtree(config.INGEST_JOB_LOCK_DIR, ignore_errors=True)


def _cleanup_stale_lock() -> None:
    if not _lock_exists():
        return
    state = _read_json(config.INGEST_JOB_STATE_JSON)
    lock = _read_lock()
    pid = lock.get("pid")
    if state and state.get("returncode") is None and _pid_is_alive(pid):
        return
    _remove_lock()


def _tail_text(path: str | Path | None, max_chars: int = _MAX_STATUS_LOG_CHARS) -> str:
    if not path:
        return ""
    log_path = Path(path)
    if not log_path.exists():
        return ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max_chars:]


def _lock_exists() -> bool:
    return config.INGEST_JOB_LOCK_DIR.exists()


def reserve_job(option: int, clean: bool) -> dict[str, Any] | None:
    """Atomically reserve one ingest slot.

    `mkdir` is atomic on local filesystems and works on Windows/Linux, so this
    protects against multiple uvicorn workers or an external scheduler racing
    with a manual button click.
    """
    if option not in OPTION_SCRIPTS:
        raise ValueError("option must be one of 1, 2, 3")
    _ensure_dirs()
    _cleanup_stale_lock()
    try:
        config.INGEST_JOB_LOCK_DIR.mkdir()
    except FileExistsError:
        return None

    run_id = uuid.uuid4().hex[:12]
    log_path = config.INGEST_RUN_LOG_DIR / f"{run_id}.log"
    state = {
        "running": True,
        "run_id": run_id,
        "option": option,
        "clean": clean,
        "returncode": None,
        "started_at": _now(),
        "finished_at": None,
        "pid": None,
        "log_path": str(log_path),
        "log": "",
        "error": None,
    }
    try:
        _write_lock(run_id, os.getpid())
        _write_json_atomic(config.INGEST_JOB_STATE_JSON, state)
        return state
    except Exception:
        _remove_lock()
        raise


def mark_finished(state: dict[str, Any], returncode: int, error: str | None = None) -> None:
    state = {**state, "running": False, "returncode": returncode, "finished_at": _now(), "error": error}
    state["log"] = _tail_text(state.get("log_path"))
    _write_json_atomic(config.INGEST_JOB_STATE_JSON, state)
    _remove_lock()


def run_reserved_job(state: dict[str, Any]) -> None:
    """Run a previously reserved job and stream combined stdout/stderr to a file."""
    args = [sys.executable, str(OPTION_SCRIPTS[int(state["option"])])]
    if state.get("clean"):
        args.append("--clean")

    log_path = Path(state["log_path"])
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            log_file.write(f"[{_now()}] start run_id={state['run_id']} option={state['option']} clean={state['clean']}\n")
            log_file.flush()
            proc = subprocess.Popen(
                args,
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            state["pid"] = proc.pid
            state["log"] = _tail_text(log_path)
            _write_lock(state["run_id"], proc.pid)
            _write_json_atomic(config.INGEST_JOB_STATE_JSON, state)

            assert proc.stdout is not None
            for line in proc.stdout:
                log_file.write(line)
                log_file.flush()
            returncode = proc.wait()
            log_file.write(f"\n[{_now()}] finish run_id={state['run_id']} returncode={returncode}\n")
        mark_finished(state, returncode)
    except Exception as exc:
        with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
            log_file.write(f"\n[{_now()}] error {type(exc).__name__}: {exc}\n")
        mark_finished(state, 1, f"{type(exc).__name__}: {exc}")


def status() -> dict[str, Any]:
    _cleanup_stale_lock()
    state = _read_json(config.INGEST_JOB_STATE_JSON)
    if not state:
        return {"running": False, "option": None, "clean": None, "returncode": None, "log": ""}

    state["log"] = _tail_text(state.get("log_path"))
    if state.get("returncode") is None:
        state["running"] = _lock_exists()
    else:
        state["running"] = False
    return state
