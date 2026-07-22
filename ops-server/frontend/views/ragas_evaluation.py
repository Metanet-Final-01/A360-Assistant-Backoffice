import streamlit as st

from components.layout import metric_strip, page_header
from views.ragas_evaluation_parts.api import get_json
from views.ragas_evaluation_parts.chunk_experiment import render_chunk_experiment_tab
from views.ragas_evaluation_parts.ragas_run import (
    fetch_ragas_runs,
    render_execution_tab,
    render_results_tab,
)


def render() -> None:
    page_header(
        "RAGAS 평가",
        "승인된 RAGAS 골드셋으로 평가를 실행하고 결과를 확인합니다.",
    )

    cases = load_ragas_cases()
    ragas_runs = fetch_ragas_runs()
    approved_case_count = count_approved_cases(cases)

    metric_strip([
        ("승인 케이스", approved_case_count),
        ("RAGAS 결과", len(ragas_runs)),
        ("결과 버전", count_result_labels(ragas_runs)),
    ])

    execution_tab, chunk_experiment_tab, results_tab = st.tabs(["평가 실행", "chunk 실험", "결과"])
    with execution_tab:
        render_execution_tab(approved_case_count)
    with chunk_experiment_tab:
        render_chunk_experiment_tab()
    with results_tab:
        render_results_tab(ragas_runs)


def load_ragas_cases() -> list[dict]:
    cases, error_message = get_json("/eval/ragas/cases")
    if error_message:
        st.warning(f"골드셋을 불러오지 못했습니다: {error_message}")
        return []
    return cases or []


def count_approved_cases(cases: list[dict]) -> int:
    return sum(
        1
        for case in cases
        if case.get("status") == "approved"
    )


def count_result_labels(ragas_runs: list[dict]) -> int:
    return len({
        run.get("agent_label")
        for run in ragas_runs
        if run.get("agent_label")
    })
