"""파이프라인 단계를 "o-o-o-o" 형태로 한눈에 보여주는 작은 시각화 컴포넌트."""

import streamlit as st

COLOR_DONE = "#12b76a"       # 초록 - 이미 끝난 단계
COLOR_RUNNING = "#f79009"    # 주황 - 지금 진행 중인 단계
COLOR_FAILED = "#f04438"     # 빨강 - 이 단계에서 실패함
COLOR_NOT_STARTED = "#d0d5dd"  # 회색 - 아직 시작 안 한 단계


def render_stage_progress(stage_names: list[str], current_stage_index: int, has_error: bool, is_running: bool) -> None:
    """stage_names: 단계 이름 목록 (예: ["업로드", "전처리", "canonical 변환", "pm4py/WorFBench 변환"]).
    current_stage_index: 지금 진행 중이거나(또는 마지막으로 진행했던) 단계 번호. 아직
    시작 전이면 -1.
    """
    circle_html_parts = []
    for index, stage_name in enumerate(stage_names):
        circle_color, is_bold = _stage_color_and_weight(index, current_stage_index, has_error, is_running)
        circle_html_parts.append(_render_one_stage(stage_name, circle_color, is_bold))

    connector_html = f'<div style="flex:1;height:3px;background:{COLOR_NOT_STARTED};margin-top:14px;"></div>'
    row_html = connector_html.join(circle_html_parts)

    st.markdown(
        f'<div style="display:flex;align-items:flex-start;gap:4px;padding:8px 0 16px 0;">{row_html}</div>',
        unsafe_allow_html=True,
    )


def _stage_color_and_weight(stage_index: int, current_stage_index: int, has_error: bool, is_running: bool) -> tuple[str, bool]:
    if stage_index < current_stage_index:
        return COLOR_DONE, False
    if stage_index > current_stage_index:
        return COLOR_NOT_STARTED, False

    # stage_index == current_stage_index: 지금 진행 중이거나 마지막으로 진행했던 단계.
    if has_error:
        return COLOR_FAILED, True
    if is_running:
        return COLOR_RUNNING, True
    return COLOR_DONE, True  # 실행이 끝났고 에러도 없으면 마지막 단계까지 전부 완료된 것


def _render_one_stage(stage_name: str, circle_color: str, is_bold: bool) -> str:
    font_weight = "700" if is_bold else "400"
    return f"""
    <div style="display:flex;flex-direction:column;align-items:center;min-width:64px;">
      <div style="
        width:28px;height:28px;border-radius:50%;
        background:{circle_color};
        border:2px solid {circle_color};
      "></div>
      <div style="margin-top:6px;font-size:12px;font-weight:{font_weight};text-align:center;white-space:nowrap;">
        {stage_name}
      </div>
    </div>
    """
