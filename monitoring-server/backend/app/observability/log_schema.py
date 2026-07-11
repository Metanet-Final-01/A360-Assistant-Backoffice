"""A360-Assistant-Backend가 이미 노출하는 모니터링용 읽기 전용 API(감사 로그/LLM 사용량/
RAG 파이프라인 로그)에서 가져온 레코드의 스키마. 셋 다 요청 메타데이터일 뿐, agent가 만든
실제 워크플로우(Recommendation) 내용은 담기지 않는다 — 그건 의도적으로 범위 밖이다.
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class AuditLogRecord(BaseModel):
    """/api/admin/audit-logs 레코드 1건 + Ops가 가져온 시각."""

    request_id: str | None = None
    user_id: str | None = None
    method: str
    path: str
    status_code: int
    latency_ms: int | None = None
    created_at: str
    fetched_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LlmUsageBreakdownItem(BaseModel):
    key: str | None = None
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class LlmUsageSnapshot(BaseModel):
    """/api/admin/llm-usage/stats는 이미 집계된 값이라 개별 레코드 중복 제거 대상이 없다 —
    수집할 때마다 타임스탬프 붙은 스냅샷 한 건으로 그대로 쌓는다."""

    snapshot_id: str
    fetched_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    period_days: int
    group_by: str
    total: dict[str, Any]
    breakdown: list[LlmUsageBreakdownItem]


class RagLogRecord(BaseModel):
    """/api/rag/logs/recent 원본 레코드. event 종류가 섞여 있어(http_request 외 파이프라인
    단계별 이벤트도 있음) EvalRunRecord.raw와 같은 이유로 raw 그대로 보존한다."""

    raw: dict[str, Any]
    fetched_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
