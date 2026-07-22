"""액션 동치 규칙(action equivalence rule) 하나의 데이터 모양.

"이름은 다르지만 같은 A360 액션"을 사람이 확인하고 등록해두는 규칙이다
(예: String.assign과 String.stringPackageAssignAction은 같은 액션으로 채점).
그냥 값 하나 바꾸는 설정이 아니라 평가 점수 자체에 영향을 주는 데이터라서,
누가 왜 등록했는지(rationale/evidence)와 지금 승인된 상태인지(status)를
같이 남긴다.
"""

from pydantic import BaseModel, Field, model_validator

# 근거 종류. 자유 텍스트 대신 이 중에서 고르게 해서 "그냥 이름이 비슷해서"류의
# 근거 없는 통합을 막는다(README의 Rule Governance 원칙).
EVIDENCE_TYPES = (
    "공식 문서",
    "액션 카탈로그",
    "실제 Bot 파일",
    "골드셋-예측 비교",
    "수동 동작 검증",
    "기타",
)

# draft: 아직 검토 전이라 채점에 반영 안 됨
# approved: 검토 끝나서 채점에 실제로 반영됨
# deprecated: 예전엔 승인됐지만 더는 안 씀(과거 평가 재현을 위해 완전히 지우지 않음)
# rejected: 검토했는데 통합하지 않기로 함
RULE_STATUSES = ("draft", "approved", "deprecated", "rejected")


class RuleEvidence(BaseModel):
    evidence_type: str = Field(min_length=1)
    reference: str = ""
    note: str = ""

    @model_validator(mode="after")
    def check_evidence_type_is_known(self) -> "RuleEvidence":
        if self.evidence_type not in EVIDENCE_TYPES:
            allowed = ", ".join(EVIDENCE_TYPES)
            raise ValueError(f"evidence_type은 다음 중 하나여야 합니다: {allowed}")
        return self


class ActionEquivalenceRule(BaseModel):
    rule_id: str = Field(min_length=1)
    canonical: str = Field(min_length=1, description="이 그룹을 대표하는 액션 이름")
    members: list[str] = Field(min_length=2, description="canonical을 포함해서 최소 2개 이상")
    status: str = "draft"
    rationale: str = Field(default="", description="왜 이 액션들을 같은 것으로 보는지")
    evidence: list[RuleEvidence] = Field(default_factory=list)

    created_by: str = ""
    created_at: str = ""
    approved_by: str | None = None
    approved_at: str | None = None

    # 이 규칙이 마지막으로 바뀐 시점의 전체 규칙 세트 버전. 평가 실행 기록에
    # 남겨서, 나중에 "이 점수는 어떤 규칙 버전 기준이었는지" 추적할 수 있게 한다.
    ruleset_version: int = 0

    @model_validator(mode="after")
    def check_rule_is_well_formed(self) -> "ActionEquivalenceRule":
        if self.status not in RULE_STATUSES:
            allowed = ", ".join(RULE_STATUSES)
            raise ValueError(f"status는 다음 중 하나여야 합니다: {allowed}")
        if self.canonical not in self.members:
            raise ValueError("canonical은 members 안에 포함되어 있어야 합니다")
        if len(set(self.members)) != len(self.members):
            raise ValueError("members 안에 같은 액션 이름이 중복됩니다")
        return self
