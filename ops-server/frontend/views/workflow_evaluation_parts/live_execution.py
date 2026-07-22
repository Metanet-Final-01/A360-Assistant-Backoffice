"""실제 Backend Agent를 호출해서 pm4py/WorFBench로 바로 채점하는 탭."""

import streamlit as st

from components.layout import card, section_header

from .api import clear_runs_cache, get_json, post_json, render_live_log

STATUS_PATH = "/eval/workflow/execution/status"


def render_live_execution_tab() -> None:
    with card("workflow_live_execution"):
        section_header(
            "Workflow 정확도 평가 실행 — 라이브(pm4py·WorFBench)",
            "실제 커뮤니티 봇 기반 골드셋(a360-eval-sandbox/Metadata/goldset_from_bots.json)으로 "
            "Backend Agent에 실제 요청을 보내 예측을 만들고, pm4py/WorFBench로 바로 채점합니다.",
        )
        _render_case_count()
        _render_start_form()
        _render_status()
        render_live_log(STATUS_PATH, key="workflow_live_log")


def _render_case_count() -> None:
    cases, error_message = get_json("/eval/workflow/cases")
    if error_message:
        st.warning(f"골드셋을 불러오지 못했습니다: {error_message}")
        return
    st.caption(f"골드셋 케이스 {len(cases)}개")


def _render_start_form() -> None:
    with st.form("workflow_live_execution_form"):
        agent_label = st.text_input("결과 버전(agent_label)", value="workflow-live", key="workflow_live_agent_label")
        start_clicked = st.form_submit_button("Workflow 평가 시작(라이브)", type="primary")

    if not start_clicked:
        return

    started, error_message = post_json("/eval/workflow/execution", {"agent_label": agent_label.strip() or "workflow-live"})
    if started:
        st.success("Workflow 평가를 시작했습니다 — 케이스마다 실제 Agent 턴을 태우고 pm4py/WorFBench 채점까지 하므로 시간이 걸립니다.")
    else:
        st.error(f"평가 시작 실패: {error_message}")


def _render_status() -> None:
    if st.button("Workflow 상태 새로고침", key="workflow_live_status_refresh"):
        clear_runs_cache()

    status, error_message = get_json(STATUS_PATH)
    if error_message:
        st.warning(f"상태를 불러오지 못했습니다: {error_message}")
        return

    if status.get("running"):
        st.info("실행 중...")
    elif status.get("error"):
        st.error(f"평가 실패: {status['error']}")
    elif status.get("finished_at"):
        st.success(f"평가 완료 · {status.get('saved', 0)}건 저장")
    else:
        st.caption("아직 실행한 라이브 Workflow 평가가 없습니다.")
