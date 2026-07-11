import streamlit as st


def render_sidebar() -> None:
    """왼쪽 사이드바 맨 위에 표시할 간단한 앱 소개. 페이지 목록은 st.navigation이 자동으로 그 아래에 그린다.
    app-kicker 클래스는 page_header와 같은 것을 재사용 — 브랜드 라벨 톤을 통일."""
    with st.sidebar:
        st.markdown('<div class="app-kicker">A360 ASSISTANT</div>', unsafe_allow_html=True)
        st.markdown("### Ops")
        st.caption("RAG 적재 · 평가 결과 · 모니터링 로그 조회 도구")
