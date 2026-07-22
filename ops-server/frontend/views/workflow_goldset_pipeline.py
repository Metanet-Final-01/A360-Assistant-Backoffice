import streamlit as st

from components.layout import page_header
from views.workflow_goldset_pipeline_parts.case_review import render_case_review_tab
from views.workflow_goldset_pipeline_parts.dataset_sets import render_dataset_sets_tab
from views.workflow_goldset_pipeline_parts.execution import render_execution_section
from views.workflow_goldset_pipeline_parts.results import render_results_section


def render() -> None:
    page_header(
        "Workflow 정답셋",
        "A360 봇 zip을 canonical 형식으로 정리하고 pm4py/WorFBench 채점용 데이터로 변환한 뒤, "
        "검수를 거쳐 평가 세트로 묶습니다.",
    )
    generate_tab, review_tab, dataset_tab = st.tabs(["정답 생성", "케이스 검수", "평가 세트"])
    with generate_tab:
        render_execution_section()
        st.divider()
        render_results_section()
    with review_tab:
        render_case_review_tab()
    with dataset_tab:
        render_dataset_sets_tab()
