"""수집한 모니터링 로그 저장/조회. app/eval/log_store.py와 같은 JSONL append-only 방식 —
별도 DB 없이 파일 하나로 충분히 가볍고, grep/cat으로도 직접 들여다볼 수 있다.
"""

import json
import uuid
from pathlib import Path

from .log_schema import AuditLogRecord, LlmUsageSnapshot, RagLogRecord

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
AUDIT_LOG_PATH = _DATA_DIR / "observability_audit_logs.jsonl"
LLM_USAGE_PATH = _DATA_DIR / "observability_llm_usage_snapshots.jsonl"
RAG_LOG_PATH = _DATA_DIR / "observability_rag_logs.jsonl"


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [line for line in (l.strip() for l in f) if line]


def _append_lines(path: Path, lines: list[str]) -> None:
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


# ---------------- audit logs ----------------


def _audit_key(r: AuditLogRecord) -> str:
    return r.request_id or f"{r.user_id}|{r.method}|{r.path}|{r.status_code}|{r.created_at}"


def append_audit_logs(records: list[AuditLogRecord]) -> int:
    """이미 저장된 것과 겹치지 않는 레코드만 append한다. 새로 추가된 건수를 반환."""
    existing_keys = {_audit_key(AuditLogRecord.model_validate_json(line)) for line in _read_lines(AUDIT_LOG_PATH)}
    new_lines = [r.model_dump_json() for r in records if _audit_key(r) not in existing_keys]
    _append_lines(AUDIT_LOG_PATH, new_lines)
    return len(new_lines)


def load_audit_logs(
    limit: int = 200,
    method: str | None = None,
    status_code: int | None = None,
    user_id: str | None = None,
) -> list[AuditLogRecord]:
    records = [AuditLogRecord.model_validate_json(line) for line in _read_lines(AUDIT_LOG_PATH)]
    if method:
        records = [r for r in records if r.method == method.upper()]
    if status_code is not None:
        records = [r for r in records if r.status_code == status_code]
    if user_id:
        records = [r for r in records if r.user_id == user_id]
    records.sort(key=lambda r: r.created_at, reverse=True)
    return records[:limit]


# ---------------- llm usage snapshots ----------------


def append_llm_usage_snapshot(snapshot_data: dict) -> LlmUsageSnapshot:
    snapshot = LlmUsageSnapshot(snapshot_id=uuid.uuid4().hex[:12], **snapshot_data)
    _append_lines(LLM_USAGE_PATH, [snapshot.model_dump_json()])
    return snapshot


def load_llm_usage_snapshots(group_by: str | None = None, limit: int = 50) -> list[LlmUsageSnapshot]:
    records = [LlmUsageSnapshot.model_validate_json(line) for line in _read_lines(LLM_USAGE_PATH)]
    if group_by:
        records = [r for r in records if r.group_by == group_by]
    records.sort(key=lambda r: r.fetched_at, reverse=True)
    return records[:limit]


# ---------------- rag pipeline logs ----------------


def _rag_key(r: RagLogRecord) -> tuple:
    raw = r.raw
    return (raw.get("request_id"), raw.get("event"), raw.get("started_at") or raw.get("timestamp"))


def append_rag_logs(records: list[RagLogRecord]) -> int:
    existing_keys = {_rag_key(RagLogRecord.model_validate_json(line)) for line in _read_lines(RAG_LOG_PATH)}
    new_lines = [r.model_dump_json() for r in records if _rag_key(r) not in existing_keys]
    _append_lines(RAG_LOG_PATH, new_lines)
    return len(new_lines)


def load_rag_logs(event: str | None = None, path_contains: str | None = None, limit: int = 200) -> list[RagLogRecord]:
    records = [RagLogRecord.model_validate_json(line) for line in _read_lines(RAG_LOG_PATH)]
    if event:
        records = [r for r in records if r.raw.get("event") == event]
    if path_contains:
        records = [r for r in records if path_contains in (r.raw.get("path") or "")]
    records.sort(key=lambda r: r.fetched_at, reverse=True)
    return records[:limit]
