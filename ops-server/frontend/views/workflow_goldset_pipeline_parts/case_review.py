"""파이프라인이 만든 draft 케이스를 검수해서 승인/폐기/반려하는 탭.

canonical 스텝은 순서가 있는 트리 구조라 통째로 폼으로 고쳐 쓰게 만들지 않고,
읽기 전용 표로 보여주기만 한다 — 구조를 바꾸고 싶으면 원본 zip/텍스트를
고쳐서 다시 변환하는 게 맞다(잘못 손으로 고치면 pm4py/WorFBench 결과물과
어긋난다). 여기서 할 수 있는 건 "이 케이스를 쓸지 말지" 판단뿐이다.
"""

import pandas as pd
import streamlit as st

from components.layout import card, section_header

from .api import get_json, post_json

STATUS_LABELS = {"draft": "검토 전", "approved": "승인됨", "deprecated": "폐기됨", "rejected": "반려됨"}


def render_case_review_tab() -> None:
    cases, error_message = get_json("/eval/workflow-goldset-pipeline/cases")
    if error_message:
        st.warning(f"케이스 목록을 불러오지 못했습니다: {error_message}")
        return

    with card("goldset_case_table"):
        section_header("정답 케이스 목록", "행을 선택하면 canonical 스텝을 확인하고 승인/폐기/반려할 수 있습니다.")
        if not cases:
            st.info("아직 생성된 케이스가 없습니다. '정답 생성' 탭에서 먼저 zip이나 텍스트를 변환하세요.")
            return

        status_filter = st.selectbox("상태 필터", ["전체", *STATUS_LABELS.keys()], format_func=lambda s: STATUS_LABELS.get(s, s), key="goldset_case_status_filter")
        filtered_cases = cases if status_filter == "전체" else [case for case in cases if case["status"] == status_filter]

        table_rows = [_build_case_row(case) for case in filtered_cases]
        selection = st.dataframe(
            pd.DataFrame(table_rows), width="stretch", hide_index=True,
            on_select="rerun", selection_mode="single-row", key=f"goldset_case_table_{status_filter}",
        )
        selected_rows = selection.selection.rows if selection else []
        if selected_rows:
            render_case_detail(filtered_cases[selected_rows[0]])


def _build_case_row(case: dict) -> dict:
    return {
        "원본": case["source_label"],
        "상태": STATUS_LABELS.get(case["status"], case["status"]),
        "canonical 스텝 수": case["canonical_step_count"],
        "pm4py leaf 수": case["pm4py_leaf_count"],
        "worfbench 충실도": case["worfbench_fidelity"],
        "생성자": case.get("created_by") or "-",
    }


def render_case_detail(case: dict) -> None:
    st.divider()
    st.subheader(f"상세 - {case['source_label']} ({STATUS_LABELS.get(case['status'], case['status'])})")

    step_rows = _flatten_steps_for_display(case["canonical_steps"])
    st.dataframe(pd.DataFrame(step_rows), width="stretch", hide_index=True, height=300)

    if case.get("review_note"):
        st.caption(f"이전 검수 메모: {case['review_note']}")

    actor = st.text_input("처리자 이름", key=f"goldset_case_actor_{case['case_id']}")
    note = st.text_input("검수 메모(선택)", key=f"goldset_case_note_{case['case_id']}")
    button_columns = st.columns(4)
    button_specs = [
        ("승인", "approved", button_columns[0]),
        ("폐기", "deprecated", button_columns[1]),
        ("반려", "rejected", button_columns[2]),
        ("검토 전으로 되돌리기", "draft", button_columns[3]),
    ]
    for label, new_status, column in button_specs:
        if column.button(label, key=f"goldset_case_{new_status}_{case['case_id']}", disabled=case["status"] == new_status):
            _change_case_status(case["case_id"], new_status, actor, note)


def _flatten_steps_for_display(steps: list[dict], depth: int = 0, branch_path: str = "root") -> list[dict]:
    """canonical 스텝(중첩 가능)을 평평한 표로 펼친다 — depth로 들여쓰기를 표현해서
    중첩 구조가 있다는 걸 알아볼 수 있게 한다."""
    rows: list[dict] = []
    for order, step in enumerate(steps, start=1):
        indent = "　" * depth  # 전각 공백으로 들여쓰기 표현
        step_type = step.get("type")
        label = f"{step.get('package')}.{step.get('action')}" if step_type == "action" else f"[{step_type}]"
        rows.append({
            "순서": order, "구조": f"{indent}{label}", "branch_path": branch_path,
            "비활성화됨": step.get("disabled", False),
        })
        child_steps = step.get("steps") or []
        if child_steps:
            rows.extend(_flatten_steps_for_display(child_steps, depth + 1, f"{branch_path}.steps"))
        for branch in step.get("branches") or []:
            branch_name = branch.get("branch") or "?"
            rows.extend(_flatten_steps_for_display(branch.get("steps") or [], depth + 1, f"{branch_path}.{branch_name}"))
    return rows


def _change_case_status(case_id: str, new_status: str, actor: str, note: str) -> None:
    succeeded, error_message = post_json(f"/eval/workflow-goldset-pipeline/cases/{case_id}/status", {
        "status": new_status, "actor": actor, "note": note,
    })
    if succeeded:
        st.success("상태를 변경했습니다.")
        st.rerun()
    else:
        st.error(f"상태 변경 실패: {error_message}")
