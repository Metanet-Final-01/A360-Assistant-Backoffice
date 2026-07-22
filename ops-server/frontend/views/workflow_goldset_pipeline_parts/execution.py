"""zip 업로드 또는 텍스트 붙여넣기로 파이프라인을 실행하는 폼."""

import streamlit as st

from components.layout import card, section_header

from . import step_visual
from .api import fetch_status, post_file_upload, post_json
from .live_log import render_polling_log, render_sse_log, render_status_summary

STATUS_PATH = "/eval/workflow-goldset-pipeline/execution/status"
EVENTS_PATH = "/eval/workflow-goldset-pipeline/execution/events"
UPLOAD_ZIP_PATH = "/eval/workflow-goldset-pipeline/execution/upload-zip"
PASTE_TEXT_PATH = "/eval/workflow-goldset-pipeline/execution/paste-text"

INPUT_MODE_ZIP = "zip 파일 업로드"
INPUT_MODE_TEXT = "워크플로우 JSON 텍스트 붙여넣기"


def render_execution_section() -> None:
    with card("goldset_pipeline_run"):
        section_header(
            "워크플로우 골든데이터셋 파이프라인",
            "A360 봇 zip(또는 워크플로우 JSON 원문)을 받아서 canonical 변환 -> "
            "pm4py/WorFBench 변환까지 실행합니다.",
        )

        status = fetch_status(STATUS_PATH)
        _render_stage_visual(status)

        input_mode = st.radio("입력 방식", [INPUT_MODE_ZIP, INPUT_MODE_TEXT], horizontal=True, key="goldset_pipeline_input_mode")
        if input_mode == INPUT_MODE_ZIP:
            _render_zip_upload_form()
        else:
            _render_text_paste_form()

        render_status_summary(status)

    render_sse_log("goldset_pipeline_sse_log", EVENTS_PATH)
    render_goldset_pipeline_polling_log()


def _render_stage_visual(status: dict | None) -> None:
    stage_names = (status or {}).get("stages") or []
    if not stage_names:
        return
    stage_index = (status or {}).get("stage_index", -1)
    has_error = bool((status or {}).get("error"))
    is_running = bool((status or {}).get("running"))
    step_visual.render_stage_progress(stage_names, stage_index, has_error, is_running)


def _render_zip_upload_form() -> None:
    with st.form("goldset_pipeline_zip_form"):
        uploaded_file = st.file_uploader("A360 봇 zip 파일", type=["zip"], key="goldset_pipeline_zip_file")
        start_clicked = st.form_submit_button("파이프라인 실행", type="primary")

    if not start_clicked:
        return
    if uploaded_file is None:
        st.error("zip 파일을 선택하세요.")
        return

    started, error_message = post_file_upload(UPLOAD_ZIP_PATH, uploaded_file.name, uploaded_file.getvalue())
    if started:
        st.success("파이프라인을 시작했습니다. 아래 로그에서 진행 상황을 확인하세요.")
    else:
        st.error(f"시작 실패: {error_message}")


def _render_text_paste_form() -> None:
    with st.form("goldset_pipeline_text_form"):
        source_label = st.text_input("이름(구분용, 선택)", value="", key="goldset_pipeline_text_label")
        raw_workflow_json = st.text_area(
            "A360 워크플로우 JSON 원문 (manifest.json 안 taskbot/headlessbot/workflow 파일 내용)",
            height=240,
            key="goldset_pipeline_text_body",
        )
        start_clicked = st.form_submit_button("파이프라인 실행", type="primary")

    if not start_clicked:
        return
    if not raw_workflow_json.strip():
        st.error("워크플로우 JSON을 붙여넣으세요.")
        return

    payload = {"raw_workflow_json": raw_workflow_json}
    if source_label.strip():
        payload["source_label"] = source_label.strip()

    started, error_message = post_json(PASTE_TEXT_PATH, payload)
    if started:
        st.success("파이프라인을 시작했습니다. 아래 로그에서 진행 상황을 확인하세요.")
    else:
        st.error(f"시작 실패: {error_message}")


@st.fragment(run_every="2s")
def render_goldset_pipeline_polling_log() -> None:
    render_polling_log("goldset_pipeline_polling_log", STATUS_PATH)
