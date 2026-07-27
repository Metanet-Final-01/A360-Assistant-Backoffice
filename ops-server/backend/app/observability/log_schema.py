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


class RequestMetricRecord(BaseModel):
    """/api/admin/request-metrics 레코드 1건 — raw HTTP 요청 성능('오늘 실시간' 패널용).
    id는 백엔드 PK라 증분 수집·중복 제거 키로 쓴다."""

    id: int
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


class MetricsDailyRecord(BaseModel):
    """/api/admin/metrics-daily 레코드 1건(일자×method×path 롤업) — 이미 집계된 값이라
    LlmUsageSnapshot과 같은 이유로 수집 시각 붙여 스냅샷으로 쌓는다(개별 중복 제거 없음 —
    같은 날짜·경로가 재수집될 때마다 최신 집계로 덮어써야 의미 있는데, 그건 조회 시
    day+method+path 최신 fetched_at만 남기는 방식으로 처리한다)."""

    day: str
    method: str
    path: str
    calls: int
    err_4xx: int
    err_5xx: int
    p50_ms: int | None = None
    p95_ms: int | None = None
    avg_ms: int | None = None
    max_ms: int | None = None
    fetched_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class UsageDailyRecord(BaseModel):
    """/api/admin/usage-daily 레코드 1건(일자×component×purpose×model 롤업)."""

    day: str
    component: str
    purpose: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    fetched_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RagEventRecord(BaseModel):
    """/api/admin/rag-events(RPA-128) 레코드 1건 — RAG 파이프라인 단계(embed/search/
    rerank) 소요·설정. id는 백엔드 PK라 RequestMetricRecord와 같은 방식으로 증분
    수집·중복 제거 키로 쓴다. detail은 이미 백엔드에서 마스킹된 JSON 문자열."""

    id: int
    request_id: str | None = None
    event: str
    function: str | None = None
    status: str | None = None
    duration_ms: float | None = None
    detail: str | None = None
    created_at: str | None = None
    fetched_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TurnEventRecord(BaseModel):
    """/api/admin/turn-events 레코드 1건 — 에이전트 턴 노드 타임라인."""

    session_id: str | None = None
    request_id: str | None = None
    seq: int
    kind: str
    stage: str | None = None
    message: str | None = None
    detail: str | None = None
    elapsed_ms: int
    created_at: str | None = None
    fetched_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
