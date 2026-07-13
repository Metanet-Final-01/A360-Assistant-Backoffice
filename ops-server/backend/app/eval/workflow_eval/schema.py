"""Workflow(pm4py/WorFBench) 골드셋 케이스 스키마 — goldset_from_bots.json의
실측 필드 구조를 그대로 따른다(catalog_coverage/scoreable은 원래 카탈로그 대조
스크립트가 채우던 값이라, 수동 등록 시엔 신뢰도가 낮음을 명시하는 용도로만 둔다)."""

from pydantic import BaseModel, Field


class WorkflowExpectedAction(BaseModel):
    package: str = Field(min_length=1)
    action: str = Field(min_length=1)
    in_catalog: bool = True


class WorkflowExpected(BaseModel):
    packages: list[str] = Field(default_factory=list)
    packages_in_catalog: list[str] = Field(default_factory=list)
    actions: list[WorkflowExpectedAction] = Field(min_length=1)


class WorkflowInput(BaseModel):
    task: str = Field(min_length=1)


class WorkflowCase(BaseModel):
    id: str = Field(min_length=1)
    source_bot: str = Field(min_length=1)
    difficulty: str = "medium"
    input: WorkflowInput
    expected: WorkflowExpected
    catalog_coverage: float | None = None
    scoreable: bool = True
