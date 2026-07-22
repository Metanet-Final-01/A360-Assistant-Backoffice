import streamlit as st

from components.layout import page_header
from views.workflow_evaluation_parts.file_replay import render_file_replay_tab
from views.workflow_evaluation_parts.live_execution import render_live_execution_tab
from views.workflow_evaluation_parts.results import render_results_tab


def render() -> None:
    page_header(
        "Workflow 평가",
        "pm4py·WorFBench로 워크플로우 정확도를 채점하고 결과를 비교합니다.",
    )
    live_tab, file_replay_tab, results_tab = st.tabs(["라이브 평가", "예측 파일 재채점", "결과"])
    with live_tab:
        render_live_execution_tab()
    with file_replay_tab:
        render_file_replay_tab()
    with results_tab:
        render_results_tab()
