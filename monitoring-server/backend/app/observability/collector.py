"""수집 오케스트레이션 + "마지막 수집 언제/몇 건" 상태. app/main.py의 _run_state와 같은
발상이지만 여기는 폴링용이 아니라 버튼 클릭 → 즉시 완료라 상태는 결과 표시용으로만 쓴다.
"""

from datetime import datetime, timezone

from . import backend_client, log_store
from .log_schema import AuditLogRecord, RagLogRecord

_collect_state: dict[str, dict] = {
    "audit_logs": {"last_collected_at": None, "fetched": 0, "new": 0, "error": None},
    "llm_usage": {"last_collected_at": None, "fetched": 0, "new": 0, "error": None},
    "rag_logs": {"last_collected_at": None, "fetched": 0, "new": 0, "error": None},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_result(source: str, fetched: int, new: int, error: str | None = None) -> dict:
    _collect_state[source] = {"last_collected_at": _now(), "fetched": fetched, "new": new, "error": error}
    return _collect_state[source]


def collect_audit_logs(limit: int = 500, method: str | None = None, status_code: int | None = None, user_id: str | None = None) -> dict:
    try:
        data = backend_client.fetch_audit_logs(limit=limit, method=method, status_code=status_code, user_id=user_id)
        records = [AuditLogRecord(**r) for r in data.get("logs", [])]
        new_count = log_store.append_audit_logs(records)
        return _record_result("audit_logs", len(records), new_count)
    except Exception as e:  # noqa: BLE001 - 백엔드 응답 스키마가 안 맞는 경우(pydantic 검증
        # 실패)도 인증/연결 실패와 마찬가지로 "이번 수집은 실패했다"는 상태로 남겨야 한다.
        _record_result("audit_logs", 0, 0, error=str(e))
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


def status() -> dict:
    return {**_collect_state, "credentials_configured": backend_client.credentials_configured()}
