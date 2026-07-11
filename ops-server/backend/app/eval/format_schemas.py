"""pm4py/WorFBench 채점 형식 엄격 검증.

이 파일이 검증하는 대상은 두 종류다.

1. "입력" 형식 — 채점 엔진에 넣기 전 준비해야 하는 데이터
   (PM4pyPredictedActions, WorfbenchPredTrajEntry). adapters.py가 Recommendation을
   변환한 결과를 이 스키마로 검증해 변환기 자체가 잘못된 형식을 만들지 않게 한다.
2. "출력" 형식 — 채점 엔진이 실제로 낸 결과, EvalRunRecord.raw에 그대로 보존되는 값
   (PM4pyConformanceResult, WorfbenchEvalResult). validate_format()이 이 스키마로
   기록 시점의 raw를 검증한다.

다른 채점 방법(rule_check/manual/커스텀)은 원래 설계대로 형식을 가리지 않는다 —
_VALIDATORS에 등록된 source(pm4py, worfbench)에 대해서만 엄격하게 검증한다
(log_schema.py 참고: "채점 로직 자체는 옮기지 않고 그릇만 만든다"는 설계를 그대로 따름).
"""

import re

from pydantic import BaseModel, Field, ValidationError, model_validator

# ---------------- pm4py: 입력 (predicted_actions) ----------------


class PM4pyAction(BaseModel):
    package: str = Field(min_length=1)
    action: str = Field(min_length=1)


class PM4pyPredictedActions(BaseModel):
    source_bot: str = Field(min_length=1)
    predicted_actions: list[PM4pyAction] = Field(min_length=1)
    predicted_action_count: int

    @model_validator(mode="after")
    def _count_matches(self) -> "PM4pyPredictedActions":
        if self.predicted_action_count != len(self.predicted_actions):
            raise ValueError(
                f"predicted_action_count({self.predicted_action_count})가 "
                f"predicted_actions 길이({len(self.predicted_actions)})와 다릅니다"
            )
        return self


# ---------------- pm4py: 출력 (conformance result) ----------------


class PM4pyConformanceResult(BaseModel):
    source_bot: str = Field(min_length=1)
    fitness: float | None = Field(None, ge=0.0, le=1.0)
    precision: float | None = Field(None, ge=0.0, le=1.0)
    gold_action_count: int | None = None
    predicted_action_count: int | None = None
    status: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _has_a_score_or_a_status(self) -> "PM4pyConformanceResult":
        if self.fitness is None and self.precision is None and self.status is None:
            raise ValueError("fitness/precision/status 중 최소 하나는 있어야 합니다")
        return self


# ---------------- WorFBench: 입력 (pred_traj) ----------------

_NODE_EDGE_PATTERN = re.compile(r"Node\s*:.*Edges\s*:", re.DOTALL)


class WorfbenchMetaAction(BaseModel):
    package: str = Field(min_length=1)
    action: str = Field(min_length=1)
    in_catalog: bool


class WorfbenchMeta(BaseModel):
    source_bot: str = Field(min_length=1)
    packages: list[str] = Field(min_length=1)
    packages_in_catalog: list[str] = Field(default_factory=list)
    actions: list[WorfbenchMetaAction] = Field(min_length=1)


class WorfbenchMessage(BaseModel):
    role: str
    content: str = Field(min_length=1)

    @model_validator(mode="after")
    def _known_role(self) -> "WorfbenchMessage":
        if self.role not in ("system", "user", "assistant"):
            raise ValueError(f"role은 system/user/assistant 중 하나여야 합니다: {self.role!r}")
        return self


class WorfbenchQuery(BaseModel):
    source: str = Field(min_length=1)
    id: str = Field(min_length=1)
    conversations: list[WorfbenchMessage] = Field(min_length=3)
    meta: WorfbenchMeta

    @model_validator(mode="after")
    def _assistant_has_node_edges(self) -> "WorfbenchQuery":
        assistant_msgs = [m for m in self.conversations if m.role == "assistant"]
        if not assistant_msgs:
            raise ValueError("conversations에 assistant 메시지가 없습니다")
        if not any(_NODE_EDGE_PATTERN.search(m.content) for m in assistant_msgs):
            raise ValueError(
                "assistant 메시지 중 'Node: ... Edges: ...' 그래프 형식을 담은 것이 없습니다"
            )
        return self


class WorfbenchPredTrajEntry(BaseModel):
    query: WorfbenchQuery


# ---------------- WorFBench: 출력 (eval result) ----------------


class WorfbenchEvalResult(BaseModel):
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1_score: float = Field(ge=0.0, le=1.0)


# ---------------- EvalRunRecord.raw 검증 (기록 시점) ----------------

_VALIDATORS: dict[str, type[BaseModel]] = {
    "pm4py": PM4pyConformanceResult,
    "worfbench": WorfbenchEvalResult,
}


def _format_pydantic_errors(exc: ValidationError) -> list[str]:
    messages = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        messages.append(f"{loc}: {err['msg']}")
    return messages


def validate_format(source: str, raw: dict | None) -> list[str]:
    """source가 pm4py/worfbench로 알려진 채점 방법일 때만 raw를 엄격히 검증한다.
    반환값이 비어있으면 통과. 그 외 source는 검증하지 않는다(형식을 안 가리는 설계 유지)."""
    model = _VALIDATORS.get(source)
    if model is None:
        return []
    if raw is None:
        return [
            f"source={source!r}는 raw에 채점 엔진 원본 출력이 있어야 합니다 "
            f"(app/eval/format_examples/{source}/ 참고)"
        ]
    try:
        model.model_validate(raw)
        return []
    except ValidationError as exc:
        return _format_pydantic_errors(exc)
