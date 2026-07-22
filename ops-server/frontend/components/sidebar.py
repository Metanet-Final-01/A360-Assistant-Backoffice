import base64

import streamlit as st

# st.logo()는 로고 이미지를 st.navigation 네비게이션과 별도의 행(stSidebarHeader)에 그려서
# 텍스트·뱃지와 같은 줄에 놓을 수가 없어서, 아이콘·타이틀·뱃지를 전부 진짜 HTML로 한 줄에 직접
# 그린다. st.markdown(unsafe_allow_html=True)은 내부 래퍼가 콘텐츠 높이를 잘못 계산해 네비
# 게이션과 겹쳤고(한 줄 텍스트 기준 높이로 고정됨), st.html()로 바꾸면 그 버그는 없어지지만
# DOMPurify가 <svg> 태그를 통째로 지워버린다 — 그래서 로고는 인라인 SVG 대신 배경 이미지(data
# URI)로 넣는다.
_MARK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
    '<defs><linearGradient id="mark" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#1f6f8b"/><stop offset="1" stop-color="#172026"/>'
    "</linearGradient></defs>"
    '<rect width="40" height="40" rx="11" fill="url(#mark)"/>'
    '<polyline points="9,22 14,22 17,13 21,29 24,19 29,19" fill="none" stroke="#ffffff" '
    'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>"
)
_MARK_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(_MARK_SVG.encode()).decode()


def render_sidebar() -> None:
    """st.navigation이 그리는 페이지 목록 바로 위에 브랜드 행(로고+타이틀+OPS 뱃지)을 한 줄로
    붙인다. 여기서 그리는 내용(stSidebarUserContent)은 원래 네비게이션보다 아래에 붙지만,
    apply_global_styles()의 CSS order로 그 사이로 끌어올린다."""
    with st.sidebar:
        st.html(
            '<div class="app-sidebar-brand">'
            f'<span class="app-sidebar-brand__mark" style="background-image: url(\'{_MARK_DATA_URI}\')"></span>'
            '<span class="app-sidebar-brand__text">'
            '<span class="app-sidebar-brand__title">A360 Assistant</span>'
            '<span class="app-sidebar-brand__subtitle">Ops Console</span>'
            "</span>"
            "</div>"
        )
        user_email = st.session_state.get("ops_user_email")
        if user_email:
            st.caption(user_email)
            if st.button("로그아웃", use_container_width=True):
                from components.auth import logout

                logout()
