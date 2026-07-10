import streamlit as st


def render_sidebar() -> None:
    """왼쪽 사이드바 맨 위에 표시할 간단한 앱 소개. 페이지 목록은 st.navigation이 자동으로 그 아래에 그린다."""
    with st.sidebar:
        st.caption("A360 Assistant Ops")
        st.caption("RAG 적재 · 평가 결과 조회 도구")
