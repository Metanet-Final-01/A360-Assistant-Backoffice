"""zip/텍스트 파이프라인이 만들어낸 워크플로우 정답 케이스 하나의 데이터 모양.

pm4py/WorFBench 변환까지 끝난 워크플로우 하나를, 사람이 검수해서 실제 평가에
쓸지 말지 정할 수 있는 "케이스"로 다룬다. 변환이 됐다고 자동으로 정답이 되는
게 아니라(변환 자체가 실패했거나 이상하게 됐을 수 있다), 검수를 거쳐야
approved가 되고, 그때부터 평가 세트에 담을 수 있다.
"""

from pydantic import BaseModel, Field, model_validator

CASE_STATUSES = ("draft", "approved", "deprecated", "rejected")


class WorkflowGoldsetCase(BaseModel):
    case_id: str = Field(min_length=1)
    source_label: str = Field(min_length=1, description="원본 워크플로우 파일 경로 또는 텍스트 입력 이름")
    status: str = "draft"

    canonical_steps: list[dict] = Field(description="canonical_convert.normalize_workflow()가 만든 순서 있는 스텝 목록")
    canonical_step_count: int = 0
    pm4py_leaf_count: int = 0
    worfbench_fidelity: str = ""
    worfbench_action_count: int = 0

    run_id: str = Field(min_length=1, description="어느 파이프라인 실행(workflow_goldset_pipeline_runs/<run_id>)에서 나왔는지")
    review_note: str = ""

    created_by: str = ""
    created_at: str = ""
    approved_by: str | None = None
    approved_at: str | None = None

    @model_validator(mode="after")
    def check_status_is_known(self) -> "WorkflowGoldsetCase":
        if self.status not in CASE_STATUSES:
            allowed = ", ".join(CASE_STATUSES)
            raise ValueError(f"status는 다음 중 하나여야 합니다: {allowed}")
        return self
