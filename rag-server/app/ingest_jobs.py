from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
STORE_DIR = Path(os.getenv("RAG_JOB_STORE_DIR", REPO_ROOT / "data" / "rag_ingest_jobs"))
DB_PATH = STORE_DIR / "jobs.sqlite3"
LOG_DIR = STORE_DIR / "logs"

MODES = ("standard", "extended", "agent_parse")
MODE_TO_OPTION = {"standard": 1, "extended": 2, "agent_parse": 3}
OPTION_TO_MODE = {value: key for key, value in MODE_TO_OPTION.items()}

STAGES = {
    "standard": [
        ("crawl", "Crawl documents"),
        ("crawl-en", "Crawl English documents"),
        ("build-action-tree", "Build action tree"),
        ("build", "Build documents"),
        ("ingest", "Load pgvector/OpenSearch"),
    ],
    "extended": [
        ("crawl", "Crawl documents"),
        ("crawl-en", "Crawl English documents"),
        ("build-action-tree", "Build action tree"),
        ("export-naive-leaf-actions", "Extract naive leaf actions"),
        ("build", "Build documents"),
        ("ingest", "Load pgvector/OpenSearch"),
    ],
    "agent_parse": [
        ("crawl", "Crawl documents"),
        ("crawl-en", "Crawl English documents"),
        ("build-action-tree", "Build action tree"),
        ("export-for-agent", "Create agent input"),
        ("parse-docs-agent", "Parse documents with LLM"),
        ("export-naive-leaf-actions", "Extract naive leaf actions"),
        ("build", "Build documents"),
        ("ingest", "Load pgvector/OpenSearch"),
    ],
}

OPTION_SCRIPTS = {
    1: REPO_ROOT / "app" / "rag" / "scripts" / "run_option1_jar_only.py",
    2: REPO_ROOT / "app" / "rag" / "scripts" / "run_option2_with_naive_actions.py",
    3: REPO_ROOT / "app" / "rag" / "scripts" / "run_option3_with_doc_agent.py",
}

TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELED", "INTERRUPTED"}
RUNNING_STATUSES = {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}

_lock = threading.RLock()
_processes: dict[str, subprocess.Popen] = {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma busy_timeout = 30000")
    conn.execute("pragma journal_mode = wal")
    conn.execute("pragma synchronous = normal")
    return conn


def init_store() -> None:
    with _connect() as conn:
        conn.execute(
            """
            create table if not exists jobs (
                job_id text primary key,
                mode text not null,
                clean integer not null,
                status text not null,
                current_stage text,
                started_at text,
                finished_at text,
                requested_by text,
                agent_parse_limit integer,
                exit_code integer,
                error_message text,
                created_at text not null,
                updated_at text not null,
                canceled_at text
            )
            """
        )
        conn.execute(
            """
            create table if not exists events (
                id integer primary key autoincrement,
                job_id text not null,
                event_type text not null,
                data text not null,
                created_at text not null
            )
            """
        )
        conn.execute("create index if not exists idx_events_job_id_id on events(job_id, id)")
        conn.execute("create index if not exists idx_jobs_created_at on jobs(created_at)")
        conn.execute(
            """
            update jobs
            set status = 'INTERRUPTED',
                finished_at = coalesce(finished_at, ?),
                updated_at = ?,
                error_message = coalesce(error_message, 'RAG server restarted while the job was running.')
            where status in ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED')
            """,
            (_utcnow(), _utcnow()),
        )


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["clean"] = bool(item["clean"])
    item["stages"] = _stages_for(item["mode"])
    item["duration_seconds"] = _duration_seconds(item)
    return item


def _duration_seconds(job: dict[str, Any]) -> int | None:
    started_at = job.get("started_at")
    if not started_at:
        return None
    end = job.get("finished_at") or _utcnow()
    try:
        start_dt = datetime.fromisoformat(started_at)
        end_dt = datetime.fromisoformat(end)
    except ValueError:
        return None
    return max(0, int((end_dt - start_dt).total_seconds()))


def _stages_for(mode: str, current_stage: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    stage_defs = STAGES.get(mode, [])
    current_index = next((i for i, (key, _) in enumerate(stage_defs) if key == current_stage), None)
    rows = []
    for index, (key, label) in enumerate(stage_defs):
        state = "pending"
        if status in TERMINAL_STATUSES and status != "SUCCEEDED":
            state = "failed" if key == current_stage and status == "FAILED" else "pending"
        elif status == "SUCCEEDED":
            state = "completed"
        elif current_index is not None:
            if index < current_index:
                state = "completed"
            elif index == current_index:
                state = "running"
        rows.append({"key": key, "label": label, "state": state})
    return rows


def _add_event(job_id: str, event_type: str, data: dict[str, Any]) -> None:
    _add_events(job_id, [(event_type, data)])


def _add_events(job_id: str, events: list[tuple[str, dict[str, Any]]]) -> None:
    if not events:
        return
    created_at = _utcnow()
    rows = []
    for event_type, data in events:
        payload = dict(data)
        payload.setdefault("job_id", job_id)
        payload.setdefault("timestamp", created_at)
        rows.append((job_id, event_type, json.dumps(payload, ensure_ascii=False), created_at))
    with _connect() as conn:
        conn.executemany(
            "insert into events(job_id, event_type, data, created_at) values (?, ?, ?, ?)",
            rows,
        )


def _update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _utcnow()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = [int(value) if isinstance(value, bool) else value for value in fields.values()]
    values.append(job_id)
    with _connect() as conn:
        conn.execute(f"update jobs set {assignments} where job_id = ?", values)


def _get_job_raw(job_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("select * from jobs where job_id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def create_job(mode: str, clean: bool, requested_by: str = "ops", agent_parse_limit: int | None = None) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError("mode must be standard, extended, or agent_parse")
    if mode == "agent_parse" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for Agent Parse mode.")

    with _lock:
        job_id = str(uuid.uuid4())
        now = _utcnow()
        if agent_parse_limit is None:
            agent_parse_limit = parse_agent_limit()
        with _connect() as conn:
            conn.execute("begin immediate")
            active = conn.execute(
                "select * from jobs where status in ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED') order by created_at desc limit 1"
            ).fetchone()
            if active:
                conn.rollback()
                raise ConflictError(active["job_id"])
            conn.execute(
                """
                insert into jobs (
                    job_id, mode, clean, status, current_stage, started_at, finished_at, requested_by,
                    agent_parse_limit, exit_code, error_message, created_at, updated_at, canceled_at
                ) values (?, ?, ?, 'QUEUED', null, null, null, ?, ?, null, null, ?, ?, null)
                """,
                (job_id, mode, int(clean), requested_by, agent_parse_limit, now, now),
            )
            conn.commit()
        _add_event(job_id, "snapshot", {"status": "QUEUED", "mode": mode, "clean": clean})
        thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
        thread.start()
        return get_job(job_id) or {"job_id": job_id, "status": "QUEUED"}


def _run_job(job_id: str) -> None:
    job = _get_job_raw(job_id)
    if not job:
        return
    option = MODE_TO_OPTION[job["mode"]]
    args = [sys.executable, str(OPTION_SCRIPTS[option])]
    if job["clean"]:
        args.append("--clean")

    log_path = log_file(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = _utcnow()
    _update_job(job_id, status="RUNNING", started_at=started_at)
    _add_event(job_id, "stage_started", {"stage": STAGES[job["mode"]][0][0], "status": "RUNNING"})

    creationflags = 0
    preexec_fn = None
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        preexec_fn = os.setsid

    exit_code = None
    pending_log_events: list[tuple[str, dict[str, Any]]] = []
    last_event_flush = time.monotonic()
    try:
        with log_path.open("a", encoding="utf-8", errors="replace") as log:
            proc = subprocess.Popen(
                args,
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                preexec_fn=preexec_fn,
            )
            with _lock:
                _processes[job_id] = proc
            for line in proc.stdout or []:
                clean_line = _mask_secrets(line.rstrip("\n"))
                log.write(clean_line + "\n")
                log.flush()
                _observe_stage(job_id, job["mode"], clean_line)
                pending_log_events.append(("log", {"level": _level_for(clean_line), "message": clean_line}))
                if len(pending_log_events) >= 50 or time.monotonic() - last_event_flush >= 1:
                    _add_events(job_id, pending_log_events)
                    pending_log_events.clear()
                    last_event_flush = time.monotonic()
            exit_code = proc.wait()
            _add_events(job_id, pending_log_events)
            pending_log_events.clear()
    except Exception as exc:
        _add_events(job_id, pending_log_events)
        _update_job(job_id, status="FAILED", finished_at=_utcnow(), error_message=str(exc), exit_code=exit_code)
        _add_event(job_id, "failed", {"status": "FAILED", "error_message": str(exc), "exit_code": exit_code})
        _clear_source_document_cache()
        return
    finally:
        with _lock:
            _processes.pop(job_id, None)

    latest = _get_job_raw(job_id) or {}
    if latest.get("status") == "CANCEL_REQUESTED":
        _update_job(job_id, status="CANCELED", finished_at=_utcnow(), exit_code=exit_code)
        _add_event(job_id, "canceled", {"status": "CANCELED", "exit_code": exit_code})
    elif exit_code == 0:
        _update_job(job_id, status="SUCCEEDED", finished_at=_utcnow(), exit_code=0)
        _add_event(job_id, "completed", {"status": "SUCCEEDED", "exit_code": 0})
    else:
        message = f"Pipeline exited with code {exit_code}."
        _update_job(job_id, status="FAILED", finished_at=_utcnow(), exit_code=exit_code, error_message=message)
        _add_event(job_id, "failed", {"status": "FAILED", "exit_code": exit_code, "error_message": message})
    _clear_source_document_cache()


def _observe_stage(job_id: str, mode: str, line: str) -> None:
    if not line.startswith("=== [") or "] " not in line:
        return
    command = line.split("] ", 1)[1].strip(" =")
    stage = _stage_from_command(mode, command)
    if not stage:
        return
    _update_job(job_id, current_stage=stage)
    _add_event(job_id, "stage_started", {"stage": stage})


def _stage_from_command(mode: str, command: str) -> str | None:
    mapping = {
        "crawl --locale en-US": "crawl-en",
        "build-action-tree": "build-action-tree",
        "export-naive-leaf-actions": "export-naive-leaf-actions",
        "export-for-agent": "export-for-agent",
        "parse-docs-agent": "parse-docs-agent",
        "build --include-naive-leaf-actions": "build",
        "build": "build",
        "ingest --clean": "ingest",
        "ingest": "ingest",
        "crawl": "crawl",
    }
    stage = mapping.get(command)
    if stage and any(item[0] == stage for item in STAGES.get(mode, [])):
        return stage
    return None


def _level_for(line: str) -> str:
    upper = line.upper()
    if "ERROR" in upper or "FAILED" in upper or "TRACEBACK" in upper:
        return "ERROR"
    if "WARN" in upper:
        return "WARNING"
    return "INFO"


def _mask_secrets(text: str) -> str:
    for key in ("OPENAI_API_KEY", "RAG_SERVICE_TOKEN", "DATABASE_PASSWORD", "RAG_DATABASE_URL"):
        value = os.getenv(key)
        if value:
            text = text.replace(value, "***")
    if "Authorization: Bearer " in text:
        return text.split("Authorization: Bearer ", 1)[0] + "Authorization: Bearer ***"
    return text


def active_job() -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "select * from jobs where status in ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED') order by created_at desc limit 1"
        ).fetchone()
    return _row_to_job(row) if row else None


def get_job(job_id: str) -> dict[str, Any] | None:
    return _get_job_raw(job_id)


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("select * from jobs order by created_at desc limit ?", (limit,)).fetchall()
    return [_row_to_job(row) for row in rows]


def list_events(job_id: str, after_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            select id, job_id, event_type, data, created_at
            from events
            where job_id = ? and id > ?
            order by id asc
            limit ?
            """,
            (job_id, after_id, limit),
        ).fetchall()
    events = []
    for row in rows:
        events.append({
            "id": row["id"],
            "job_id": row["job_id"],
            "event_type": row["event_type"],
            "data": json.loads(row["data"]),
            "created_at": row["created_at"],
        })
    return events


def read_log(job_id: str, tail: int | None = 400) -> str:
    path = log_file(job_id)
    if not path.exists():
        return ""
    try:
        if tail is None:
            return path.read_text(encoding="utf-8", errors="replace")
        text = _tail_text(path, tail)
    except OSError:
        return ""
    return text


def _tail_text(path: Path, tail: int) -> str:
    if tail <= 0:
        return ""
    block_size = 8192
    chunks = bytearray()
    newline_count = 0
    with path.open("rb") as file:
        file.seek(0, os.SEEK_END)
        position = file.tell()
        while position > 0 and newline_count <= tail:
            read_size = min(block_size, position)
            position -= read_size
            file.seek(position)
            chunk = file.read(read_size)
            chunks[:0] = chunk
            newline_count += chunk.count(b"\n")
    text = chunks.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-tail:])


def _clear_source_document_cache() -> None:
    try:
        from app.rag import source_documents

        source_documents.clear_cache()
    except Exception:
        return


def log_file(job_id: str) -> Path:
    return LOG_DIR / f"{job_id}.log"


def cancel_job(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise KeyError(job_id)
    if job["status"] in TERMINAL_STATUSES:
        return job
    _update_job(job_id, status="CANCEL_REQUESTED", canceled_at=_utcnow())
    _add_event(job_id, "warning", {"message": "Cancel requested by operator.", "status": "CANCEL_REQUESTED"})
    proc = _processes.get(job_id)
    if proc and proc.poll() is None:
        _terminate_process_tree(proc)
    return get_job(job_id) or job


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        proc.kill()


def parse_agent_limit() -> int | None:
    raw = os.getenv("AGENT_PARSE_LIMIT", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def capabilities() -> dict[str, Any]:
    return {
        "openai_api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
        "agent_parse_limit": parse_agent_limit(),
        "modes": list(MODES),
    }


class ConflictError(Exception):
    def __init__(self, job_id: str):
        super().__init__(f"job {job_id} is already running")
        self.job_id = job_id


init_store()
