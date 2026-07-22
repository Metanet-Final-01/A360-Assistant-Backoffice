"""규칙이 언제, 누가, 무엇을 바꿨는지 보는 변경 이력."""

import pandas as pd
import streamlit as st

from components.layout import card, section_header

from .api import fetch_events

EVENT_TYPE_LABELS = {"created": "등록", "status_changed": "상태 변경", "fields_updated": "내용 수정"}


def render_history_tab() -> None:
    with card("action_rules_history"):
        section_header("변경 이력", "규칙 세트가 바뀔 때마다 자동으로 기록됩니다(등록/승인/폐기/반려/수정).")
        events = fetch_events(limit=200)
        if not events:
            st.info("아직 변경 이력이 없습니다.")
            return

        rows = [_build_event_row(event) for event in events]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _build_event_row(event: dict) -> dict:
    return {
        "시각": event.get("at", "")[:19].replace("T", " "),
        "구분": EVENT_TYPE_LABELS.get(event.get("event_type"), event.get("event_type")),
        "규칙 id": event.get("rule_id"),
        "처리자": event.get("actor"),
        "버전": event.get("ruleset_version"),
        "상세": str(event.get("detail")),
    }
