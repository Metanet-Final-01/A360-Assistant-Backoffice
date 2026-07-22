"""워크플로우 정답 케이스 저장소. goldset_admin.py의 범용 읽기/쓰기 함수를
그대로 쓴다 — RAGAS/BFCL 골드셋과 같은 방식(JSON 배열 파일, 원자적 쓰기)."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .. import goldset_admin
from .case_schema import WorkflowGoldsetCase

CASES_PATH = Path(__file__).resolve().parents[3] / "data" / "workflow_goldset_cases.json"


def list_cases() -> list[dict]:
    return goldset_admin.read_raw(CASES_PATH)


def get_case(case_id: str) -> dict | None:
    return next((case for case in list_cases() if case.get("case_id") == case_id), None)


def save_new_case(
    *, source_label: str, canonical_steps: list[dict], canonical_step_count: int,
    pm4py_leaf_count: int, worfbench_fidelity: str, worfbench_action_count: int,
    run_id: str, created_by: str = "",
) -> WorkflowGoldsetCase:
    """파이프라인이 워크플로우 하나를 변환할 때마다 draft 케이스로 저장한다."""
    payload = {
        "case_id": uuid4().hex[:12],
        "source_label": source_label,
        "status": "draft",
        "canonical_steps": canonical_steps,
        "canonical_step_count": canonical_step_count,
        "pm4py_leaf_count": pm4py_leaf_count,
        "worfbench_fidelity": worfbench_fidelity,
        "worfbench_action_count": worfbench_action_count,
        "run_id": run_id,
        "created_by": created_by or "(익명)",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return goldset_admin.append_case(CASES_PATH, WorkflowGoldsetCase, payload, "case_id")


def update_case_status(case_id: str, new_status: str, actor: str, note: str = "") -> WorkflowGoldsetCase:
    patch: dict = {"status": new_status, "review_note": note}
    if new_status == "approved":
        patch["approved_by"] = actor or "(익명)"
        patch["approved_at"] = datetime.now(timezone.utc).isoformat()
    return goldset_admin.update_case(CASES_PATH, WorkflowGoldsetCase, "case_id", case_id, patch)
