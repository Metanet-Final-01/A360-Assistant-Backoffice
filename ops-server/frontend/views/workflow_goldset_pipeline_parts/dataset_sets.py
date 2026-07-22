"""승인된 케이스들을 묶어서 "평가 세트"(dataset)로 등록하는 탭.

case_id 목록을 텍스트로 입력받는 기존 방식 그대로 재사용한다(/eval/datasets,
Workflow 평가 페이지의 예측 파일 재채점 실행이 이 데이터를 그대로 읽는다).
여기서는 등록/조회만 하고, 그 데이터셋으로 실제 채점을 실행하는 건
"Workflow 평가" 페이지의 몫이다 — 정답을 만드는 것과 정답으로 채점하는 것을
분리해둔다.
"""

import streamlit as st

from components.layout import card, section_header

from .api import get_json, post_json


def render_dataset_sets_tab() -> None:
    with card("goldset_dataset_registry"):
        section_header(
            "평가 세트", "승인된 케이스의 case_id를 모아서 이름 붙여진 평가 범위로 고정합니다. "
            "'Workflow 평가' 페이지의 예측 파일 재채점에서 이 목록을 그대로 씁니다.",
        )
        _render_approved_case_ids()
        datasets, error_message = get_json("/eval/datasets")
        if error_message:
            st.warning(f"평가 세트를 불러오지 못했습니다: {error_message}")
            datasets = []

        if datasets:
            rows = [
                {"dataset_id": d["dataset_id"], "이름": d["name"], "버전": d["version"],
                 "케이스 수": len(d["case_ids"]), "설명": d.get("description") or ""}
                for d in datasets
            ]
            st.dataframe(rows, width="stretch", hide_index=True)

        with st.expander("새 평가 세트 등록", expanded=not datasets):
            _render_new_dataset_form()


def _render_approved_case_ids() -> None:
    cases, error_message = get_json("/eval/workflow-goldset-pipeline/cases")
    if error_message:
        return
    approved_case_ids = [case["case_id"] for case in (cases or []) if case["status"] == "approved"]
    st.caption(f"현재 승인된 케이스 {len(approved_case_ids)}개" + (f": {', '.join(approved_case_ids[:5])}..." if approved_case_ids else ""))


def _render_new_dataset_form() -> None:
    with st.form("goldset_dataset_form"):
        id_column, name_column, version_column = st.columns(3)
        dataset_id = id_column.text_input("dataset_id", placeholder="workflow-goldset")
        name = name_column.text_input("표시 이름", placeholder="워크플로우 골드셋")
        version = version_column.text_input("버전", placeholder="2026.07")
        description = st.text_input("설명", placeholder="평가 범위와 변경 이유")
        case_id_text = st.text_area("case_id 목록", placeholder="web_excel_email_001\ninvoice_processing_001", height=150)
        submitted = st.form_submit_button("평가 세트 등록", type="primary")

    if not submitted:
        return

    payload = {
        "dataset_id": dataset_id, "name": name, "version": version,
        "description": description or None,
        "case_ids": [line.strip() for line in case_id_text.splitlines() if line.strip()],
    }
    saved, error_message = post_json("/eval/datasets", payload)
    if saved:
        st.success("평가 세트를 등록했습니다.")
        st.rerun()
    else:
        st.error(f"등록 실패: {error_message}")
