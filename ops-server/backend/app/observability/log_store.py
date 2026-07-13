"""수집한 모니터링 로그 저장/조회. app/eval/log_store.py와 같은 JSONL append-only 방식 —
별도 DB 없이 파일 하나로 충분히 가볍고, grep/cat으로도 직접 들여다볼 수 있다.
"""

import json
import threading
import uuid
from pathlib import Path

# 관측 수집 엔드포인트는 FastAPI sync 함수라 threadpool에서 동시 실행될 수 있다.
# read-filter-append(중복 제거)가 원자적이지 않으면 동시 호출이 같은 레코드를 중복
# append하므로, 증분 수집 경로(request_metrics)의 임계구역을 이 락으로 묶는다(CodeRabbit #9).
_append_lock = threading.Lock()

from .log_schema import (
    AuditLogRecord,
    LlmUsageSnapshot,
    MetricsDailyRecord,
    RagEventRecord,
    RagLogRecord,
    RequestMetricRecord,
    TurnEventRecord,
    UsageDailyRecord,
)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
AUDIT_LOG_PATH = _DATA_DIR / "observability_audit_logs.jsonl"
LLM_USAGE_PATH = _DATA_DIR / "observability_llm_usage_snapshots.jsonl"
RAG_LOG_PATH = _DATA_DIR / "observability_rag_logs.jsonl"
METRICS_DAILY_PATH = _DATA_DIR / "observability_metrics_daily.jsonl"
USAGE_DAILY_PATH = _DATA_DIR / "observability_usage_daily.jsonl"
TURN_EVENTS_PATH = _DATA_DIR / "observability_turn_events.jsonl"
REQUEST_METRICS_PATH = _DATA_DIR / "observability_request_metrics.jsonl"
RAG_EVENTS_PATH = _DATA_DIR / "observability_rag_events.jsonl"


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


def audit_cursor() -> str | None:
    """저장된 감사 로그 중 가장 최근 created_at — 다음 증분 수집의 since로 쓴다."""
    created = [
        AuditLogRecord.model_validate_json(line).created_at for line in _read_lines(AUDIT_LOG_PATH)
    ]
    return max(created) if created else None


# ---------------- request metrics (raw, 증분) ----------------


def append_request_metrics(records: list[RequestMetricRecord]) -> int:
    """id(백엔드 PK) 기준 append-only 중복 제거 — 증분 수집이라도 겹침 방어.

    read-filter-append 전체를 락으로 묶는다 — 동시 collect가 같은 existing을 읽고
    같은 레코드를 중복 append하는 경쟁을 차단(CodeRabbit #9)."""
    with _append_lock:
        existing = {RequestMetricRecord.model_validate_json(line).id for line in _read_lines(REQUEST_METRICS_PATH)}
        new_lines = [r.model_dump_json() for r in records if r.id not in existing]
        _append_lines(REQUEST_METRICS_PATH, new_lines)
        return len(new_lines)


def load_request_metrics(method: str | None = None, path_contains: str | None = None, limit: int = 500) -> list[RequestMetricRecord]:
    records = [RequestMetricRecord.model_validate_json(line) for line in _read_lines(REQUEST_METRICS_PATH)]
    if method:
        records = [r for r in records if r.method == method.upper()]
    if path_contains:
        records = [r for r in records if path_contains in r.path]
    records.sort(key=lambda r: r.id, reverse=True)
    return records[:limit]


def request_metrics_cursor() -> str | None:
    """저장된 request_metrics 중 가장 최근 created_at — 다음 증분 수집의 since."""
    created = [
        RequestMetricRecord.model_validate_json(line).created_at for line in _read_lines(REQUEST_METRICS_PATH)
    ]
    return max(created) if created else None


# ---------------- RAG 파이프라인 이벤트 (RPA-128, raw, 증분) ----------------


def append_rag_events(records: list[RagEventRecord]) -> int:
    """id(백엔드 PK) 기준 append-only 중복 제거 — request_metrics와 동일한 이유로 락."""
    with _append_lock:
        existing = {RagEventRecord.model_validate_json(line).id for line in _read_lines(RAG_EVENTS_PATH)}
        new_lines = [r.model_dump_json() for r in records if r.id not in existing]
        _append_lines(RAG_EVENTS_PATH, new_lines)
        return len(new_lines)


def load_rag_events(request_id: str | None = None, event: str | None = None, limit: int = 500) -> list[RagEventRecord]:
    records = [RagEventRecord.model_validate_json(line) for line in _read_lines(RAG_EVENTS_PATH)]
    if request_id:
        records = [r for r in records if r.request_id == request_id]
    if event:
        records = [r for r in records if r.event == event]
    records.sort(key=lambda r: r.id, reverse=True)
    return records[:limit]


# ---------------- 사건 추적(상관관계) ----------------


def trace_by(request_id: str | None = None, session_id: str | None = None) -> dict:
    """한 사건에 연결된 관측 레코드를 종류별로 모은다 (대시보드 #5).

    - request_id: HTTP 요청 1건 축 — audit·request_metrics·turn_events·rag_logs 전부 연결.
    - session_id: 대화 축 — turn_events(그 세션의 모든 턴)만 직접 연결(감사/성능/RAG는
      요청 축이라 세션 키가 없다). 세션의 request_id들은 반환된 turn_events에서 얻는다.
    """
    def _audit():
        rows = [AuditLogRecord.model_validate_json(l) for l in _read_lines(AUDIT_LOG_PATH)]
        rows = [r for r in rows if request_id and r.request_id == request_id]
        return [r.model_dump() for r in sorted(rows, key=lambda r: r.created_at)]

    def _metrics():
        rows = [RequestMetricRecord.model_validate_json(l) for l in _read_lines(REQUEST_METRICS_PATH)]
        rows = [r for r in rows if request_id and r.request_id == request_id]
        return [r.model_dump() for r in sorted(rows, key=lambda r: r.created_at)]

    def _rag():
        rows = [RagLogRecord.model_validate_json(l) for l in _read_lines(RAG_LOG_PATH)]
        rows = [r for r in rows if request_id and r.raw.get("request_id") == request_id]
        return [r.model_dump() for r in rows]

    def _rag_events():
        rows = [RagEventRecord.model_validate_json(l) for l in _read_lines(RAG_EVENTS_PATH)]
        rows = [r for r in rows if request_id and r.request_id == request_id]
        return [r.model_dump() for r in sorted(rows, key=lambda r: r.id)]

    def _turns():
        rows = [TurnEventRecord.model_validate_json(l) for l in _read_lines(TURN_EVENTS_PATH)]
        rows = [r for r in rows
                if (request_id and r.request_id == request_id) or (session_id and r.session_id == session_id)]
        # created_at 우선 정렬 — request_id 문자열 순으로 묶으면 한 세션의 여러 요청이
        # 실제 발생 순서와 어긋난다(CodeRabbit #13). 타임스탬프 없으면 seq로 폴백.
        return [r.model_dump() for r in sorted(
            rows, key=lambda r: (r.created_at is None, r.created_at or "", r.seq),
        )]

    return {
        "request_id": request_id,
        "session_id": session_id,
        "audit_logs": _audit(),
        "request_metrics": _metrics(),
        "turn_events": _turns(),
        "rag_logs": _rag(),
        "rag_events": _rag_events(),
    }


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


# ---------------- 일별 롤업(metrics_daily/usage_daily) + turn_events ----------------
#
# 롤업은 Backend에서 멱등 재집계(DELETE+INSERT)되는 값이라, 여기서도 매번 그냥
# append만 하고 조회 시 (day, method, path) 등 키별로 가장 최근 fetched_at 한 건만
# 남긴다 — audit_logs처럼 "한 번 생기면 불변"인 이벤트가 아니라서 append-only 중복
# 제거 방식이 안 맞는다.


def _latest_by_key(records: list, key_fn) -> list:
    latest: dict = {}
    for r in records:
        k = key_fn(r)
        if k not in latest or r.fetched_at > latest[k].fetched_at:
            latest[k] = r
    return list(latest.values())


def append_metrics_daily(records: list[MetricsDailyRecord]) -> int:
    _append_lines(METRICS_DAILY_PATH, [r.model_dump_json() for r in records])
    return len(records)


def load_metrics_daily(method: str | None = None, path_contains: str | None = None, limit: int = 500) -> list[MetricsDailyRecord]:
    records = [MetricsDailyRecord.model_validate_json(line) for line in _read_lines(METRICS_DAILY_PATH)]
    records = _latest_by_key(records, lambda r: (r.day, r.method, r.path))
    if method:
        records = [r for r in records if r.method == method.upper()]
    if path_contains:
        records = [r for r in records if path_contains in r.path]
    records.sort(key=lambda r: r.day, reverse=True)
    return records[:limit]


def append_usage_daily(records: list[UsageDailyRecord]) -> int:
    _append_lines(USAGE_DAILY_PATH, [r.model_dump_json() for r in records])
    return len(records)


def load_usage_daily(component: str | None = None, limit: int = 500) -> list[UsageDailyRecord]:
    records = [UsageDailyRecord.model_validate_json(line) for line in _read_lines(USAGE_DAILY_PATH)]
    records = _latest_by_key(records, lambda r: (r.day, r.component, r.purpose, r.model))
    if component:
        records = [r for r in records if r.component == component]
    records.sort(key=lambda r: r.day, reverse=True)
    return records[:limit]


def _turn_event_key(r: TurnEventRecord) -> tuple:
    # session_id/request_id가 둘 다 없는(익명) 이벤트는 seq만으로 겹칠 수 있어
    # kind/stage/elapsed_ms까지 키에 더해 충돌 가능성을 줄인다(CodeRabbit 지적 —
    # 완벽한 보장은 아니지만, Backend가 실제로는 항상 request_id를 채워 보내므로
    # 이 경로는 방어적 차원).
    return (r.session_id, r.request_id, r.seq, r.kind, r.stage, r.elapsed_ms)


def append_turn_events(records: list[TurnEventRecord]) -> int:
    """세션 이벤트는 한 번 생기면 불변이라 audit_logs와 같은 append-only 중복 제거.
    같은 배치(records) 안에서도 겹칠 수 있어 seen을 배치 진행 중에 갱신한다 —
    기존 파일 스캔 한 번으로 만든 집합만 보면, 이번 배치 안의 중복은 못 잡는다."""
    seen = {_turn_event_key(TurnEventRecord.model_validate_json(line)) for line in _read_lines(TURN_EVENTS_PATH)}
    new_lines = []
    for r in records:
        key = _turn_event_key(r)
        if key in seen:
            continue
        seen.add(key)
        new_lines.append(r.model_dump_json())
    _append_lines(TURN_EVENTS_PATH, new_lines)
    return len(new_lines)


def load_turn_events(session_id: str | None = None, limit: int = 200) -> list[TurnEventRecord]:
    records = [TurnEventRecord.model_validate_json(line) for line in _read_lines(TURN_EVENTS_PATH)]
    if session_id:
        records = [r for r in records if r.session_id == session_id]
        records.sort(key=lambda r: (r.request_id or "", r.seq))
    else:
        records.sort(key=lambda r: r.created_at or "", reverse=True)
    return records[:limit]
