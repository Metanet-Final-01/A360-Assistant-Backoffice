import streamlit as st

from components.layout import page_header
from views.action_rules_parts.history_tab import render_history_tab
from views.action_rules_parts.rules_tab import render_rules_tab


def render() -> None:
    page_header(
        "액션 동치 규칙",
        "이름은 다르지만 같은 A360 액션인 것들을 등록해두면, pm4py/WorFBench 채점 전에 "
        "이 규칙으로 이름을 정규화합니다. 근거 없이 이름이 비슷하다는 이유만으로 합치지 않습니다.",
    )
    rules_tab, history_tab = st.tabs(["규칙", "변경 이력"])
    with rules_tab:
        render_rules_tab()
    with history_tab:
        render_history_tab()
