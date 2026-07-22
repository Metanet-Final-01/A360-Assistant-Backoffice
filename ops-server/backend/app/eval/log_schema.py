"""평가 결과 로그 레코드 — 채점 방법(rule_check/pm4py/worfbench/수작업 등)에 무관하게
같은 형태로 저장·조회·비교할 수 있게 하는 최소 공통 스키마.

지금 단계에서는 어떤 채점 엔진을 최종 채택할지 정해지지 않았다(둘 다 아직 신뢰 검증
전) — 그래서 채점 로직 자체는 옮기지 않고, "채점 결과가 이 형태로만 들어오면 조회·
비교가 된다"는 그릇만 먼저 만든다. raw에 원본 결과를 그대로 보존해 나중에 어떤
엔진이 맞는지 판단할 때 원본을 다시 들여다볼 수 있게 한다.
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator


class EvalMetric(BaseModel):
    name: str = Field(description="예: pass_rate, pm4py_fitness, worfbench_f1")
    value: float
    note: str | None = None


class EvalRunRecord(BaseModel):
    run_id: str | None = Field(None, description="이 기록 고유 id — 없으면 저장 시 자동 생성")
    logged_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    evaluation_id: str | None = Field(None, description="한 번의 배치 평가 실행을 묶는 id")
    case_execution_id: str | None = Field(
        None, description="케이스 하나를 특정 설정으로 한 번 실행한 고유 id — 관측 DB의 trace_id/request_id와 이어붙이는 용도"
    )
    trace_id: str | None = Field(None, description="이 케이스 실행이 Backend를 호출했다면, 그 호출 전체를 묶는 추적 id")
    request_ids: list[str] = Field(
        default_factory=list, description="이 케이스 실행 중 실제로 나간 HTTP request_id들(재시도로 여러 개일 수 있음)"
    )
    dataset_id: str | None = Field(None, description="평가 데이터셋 id")
    dataset_version: str | None = Field(None, description="평가 데이터셋 버전")
    case_id: str = Field(description="평가 케이스 id, 예: web_excel_email_001")
    source: str = Field(description="채점 방법 이름, 예: rule_check | pm4py | worfbench | manual")
    agent_label: str | None = Field(None, description="평가 대상 에이전트/버전, 예: dev, rpa27")
    commit_sha: str | None = Field(None, description="평가 대상 코드 커밋")
    config: dict[str, Any] = Field(default_factory=dict, description="모델·프롬프트·RAG 등 실행 설정")
    passed: bool | None = Field(None, description="있으면 pass/fail 결과")
    score: float | None = Field(None, description="있으면 0~1 사이 대표 점수 하나")
    metrics: list[EvalMetric] = Field(default_factory=list)
    raw: dict[str, Any] | None = Field(None, description="채점 엔진의 원본 출력 전체 보존")

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("source는 비어 있을 수 없습니다")
        return value
