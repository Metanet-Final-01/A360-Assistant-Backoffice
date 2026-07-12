"""k6 부하테스트 실행 결과 스키마 (RPA-115 후속 — 성능테스트를 Ops에서 직접 돌리고 기록)."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class LoadTestRunRecord(BaseModel):
    run_id: str
    label: str = Field(description="테스트 대상을 구분하는 이름 — 예: rag-search")
    target_url: str
    method: str = "GET"
    peak_vus: int
    total_requests: int
    avg_ms: float
    p50_ms: float
    p90_ms: float
    p95_ms: float
    max_ms: float
    throughput_rps: float
    error_rate: float
    log_tail: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
