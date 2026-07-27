"""수집 오케스트레이션 + "마지막 수집 언제/몇 건" 상태. app/main.py의 _run_state와 같은
발상이지만 여기는 폴링용이 아니라 버튼 클릭 → 즉시 완료라 상태는 결과 표시용으로만 쓴다.
"""

from datetime import datetime, timezone

from . import backend_client, log_store, obs_db
from .log_schema import (
    AuditLogRecord,
    MetricsDailyRecord,
    RagEventRecord,
    RagLogRecord,
    RequestMetricRecord,
    TurnEventRecord,
    UsageDailyRecord,
)

_collect_state: dict[str, dict] = {
    "audit_logs": {"last_collected_at": None, "fetched": 0, "new": 0, "error": None},
    "llm_usage": {"last_collected_at": None, "fetched": 0, "new": 0, "error": None},
    "rag_logs": {"last_collected_at": None, "fetched": 0, "new": 0, "error": None},
    "metrics_daily": {"last_collected_at": None, "fetched": 0, "new": 0, "error": None},
    "usage_daily": {"last_collected_at": None, "fetched": 0, "new": 0, "error": None},
    "turn_events": {"last_collected_at": None, "fetched": 0, "new": 0, "error": None},
    "request_metrics": {"last_collected_at": None, "fetched": 0, "new": 0, "error": None},
    "rag_events": {"last_collected_at": None, "fetched": 0, "new": 0, "error": None},
}

# 백엔드 생존 감시 — 마지막 프로브 결과와 '마지막 정상(UP)' 시각을 기억한다.
_health_state: dict = {"reachable": False, "status": "unknown", "checks": {},
                       "checked_at": None, "last_ok_at": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_result(source: str, fetched: int, new: int, error: str | None = None) -> dict:
    _collect_state[source] = {"last_collected_at": _now(), "fetched": fetched, "new": new, "error": error}
    return _collect_state[source]


def collect_audit_logs(limit: int = 500, method: str | None = None, status_code: int | None = None, user_id: str | None = None, incremental: bool = True) -> dict:
    try:
        # 증분: 저장된 마지막 created_at 이후만 요청 — 매번 최신 500건 재수집을 피한다.
        # 단, 필터(method/status/user)가 걸리면 커서가 필터별로 어긋나니 전량 조회로 폴백.
        since = log_store.audit_cursor() if (incremental and not (method or status_code or user_id)) else None
        data = backend_client.fetch_audit_logs(limit=limit, method=method, status_code=status_code, user_id=user_id, since=since)
        records = [AuditLogRecord(**r) for r in data.get("logs", [])]
        new_count = log_store.append_audit_logs(records)
        return _record_result("audit_logs", len(records), new_count)
    except Exception as e:  # noqa: BLE001 - 백엔드 응답 스키마가 안 맞는 경우(pydantic 검증
        # 실패)도 인증/연결 실패와 마찬가지로 "이번 수집은 실패했다"는 상태로 남겨야 한다.
        _record_result("audit_logs", 0, 0, error=str(e))
        raise


def collect_request_metrics(limit: int = 500, method: str | None = None, path: str | None = None, incremental: bool = True) -> dict:
    """raw 요청 메트릭 증분 수집 — '오늘 실시간' 패널의 소스(롤업 60분 지연 보완)."""
    try:
        since = log_store.request_metrics_cursor() if (incremental and not (method or path)) else None
        data = backend_client.fetch_request_metrics(since=since, limit=limit, method=method, path=path)
        records = [RequestMetricRecord(**r) for r in data.get("rows", [])]
        new_count = log_store.append_request_metrics(records)
        return _record_result("request_metrics", len(records), new_count)
    except Exception as e:  # noqa: BLE001 - collect_audit_logs와 같은 이유
        _record_result("request_metrics", 0, 0, error=str(e))
        raise


def collect_llm_usage(days: int = 30, group_by: str = "component") -> dict:
    try:
        data = backend_client.fetch_llm_usage_stats(days=days, group_by=group_by)
        snapshot = log_store.append_llm_usage_snapshot(data)
        return _record_result("llm_usage", len(snapshot.breakdown), 1)
    except Exception as e:  # noqa: BLE001 - collect_audit_logs와 같은 이유
        _record_result("llm_usage", 0, 0, error=str(e))
        raise


# rag/logs/recent가 섞어 내보내는 이벤트 중 http_request만 수집한다 — 그 외
# (embed_query/hybrid_search 등 파이프라인 단계 이벤트)에는 검색어 미리보기 같은 텍스트가
# 섞여 있을 수 있어, "요청 메타데이터만" 범위를 지키기 위한 의도적 필터.
def collect_rag_logs(limit: int = 100) -> dict:
    try:
        data = backend_client.fetch_rag_logs_recent(limit=limit)
        raw_records = [r for r in data.get("logs", []) if r.get("event") == "http_request"]
        records = [RagLogRecord(raw=r) for r in raw_records]
        new_count = log_store.append_rag_logs(records)
        return _record_result("rag_logs", len(records), new_count)
    except Exception as e:  # noqa: BLE001 - collect_audit_logs와 같은 이유
        _record_result("rag_logs", 0, 0, error=str(e))
        raise


def collect_metrics_daily(days: int = 7, method: str | None = None, path: str | None = None) -> dict:
    try:
        data = backend_client.fetch_metrics_daily(days=days, method=method, path=path)
        records = [MetricsDailyRecord(**r) for r in data.get("rows", [])]
        new_count = log_store.append_metrics_daily(records)
        return _record_result("metrics_daily", len(records), new_count)
    except Exception as e:  # noqa: BLE001 - collect_audit_logs와 같은 이유
        _record_result("metrics_daily", 0, 0, error=str(e))
        raise


def collect_usage_daily(days: int = 30, component: str | None = None, model: str | None = None) -> dict:
    try:
        data = backend_client.fetch_usage_daily(days=days, component=component, model=model)
        records = [UsageDailyRecord(**r) for r in data.get("rows", [])]
        new_count = log_store.append_usage_daily(records)
        return _record_result("usage_daily", len(records), new_count)
    except Exception as e:  # noqa: BLE001 - collect_audit_logs와 같은 이유
        _record_result("usage_daily", 0, 0, error=str(e))
        raise


def collect_turn_events(session_id: str | None = None, limit: int = 200) -> dict:
    try:
        data = backend_client.fetch_turn_events(session_id=session_id, limit=limit)
        records = [TurnEventRecord(**r) for r in data.get("events", [])]
        new_count = log_store.append_turn_events(records)
        return _record_result("turn_events", len(records), new_count)
    except Exception as e:  # noqa: BLE001 - collect_audit_logs와 같은 이유
        _record_result("turn_events", 0, 0, error=str(e))
        raise


def collect_rag_events(request_id: str | None = None, limit: int = 500) -> dict:
    """RAG 파이프라인 단계 로그(RPA-128) 수집 — turn_events와 동일하게 since 커서 없이
    최근 limit건을 매번 가져와 id 기준 append-only 중복 제거(log_store.append_rag_events)."""
    try:
        data = backend_client.fetch_rag_events(request_id=request_id, limit=limit)
        records = [RagEventRecord(**r) for r in data.get("events", [])]
        new_count = log_store.append_rag_events(records)
        return _record_result("rag_events", len(records), new_count)
    except Exception as e:  # noqa: BLE001 - collect_audit_logs와 같은 이유
        _record_result("rag_events", 0, 0, error=str(e))
        raise


def probe_backend_health() -> dict:
    """백엔드 생존 감시 프로브 — 결과를 기억하고 '마지막 정상(UP)' 시각을 갱신한다."""
    result = backend_client.probe_health()
    up = result.get("reachable") and result.get("status") in ("healthy", "degraded")
    _health_state.update({
        "reachable": result.get("reachable", False),
        "status": result.get("status", "unknown"),
        "checks": result.get("checks", {}),
        "http_status": result.get("http_status"),
        "error": result.get("error"),
        "checked_at": result.get("checked_at"),
    })
    if up:
        _health_state["last_ok_at"] = result.get("checked_at")
    return dict(_health_state)


def backend_health() -> dict:
    """마지막으로 관측된 백엔드 생존 상태(프로브 없이 캐시 반환)."""
    return dict(_health_state)


def status() -> dict:
    return {
        **_collect_state,
        "credentials_configured": backend_client.credentials_configured(),
        # 관측 DB 직접 조회 구성 여부 — 화면이 "직접 조회 미구성" 배너를 띄우는 근거다.
        # 미구성이면 조회 엔드포인트가 503을 낸다(사본으로 조용히 되돌아가지 않는다).
        "obs_db_configured": obs_db.configured(),
        "backend_health": dict(_health_state),
    }
