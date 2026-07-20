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


def _nav_key(page: st.Page) -> str:
    return f"nav_{page.url_path or 'home'}"


def render_sidebar(
    home_page: st.Page,
    sections: list[tuple[str, list[st.Page]]],
    current_page: st.Page,
) -> None:
    """브랜드 행(로고+타이틀) + 카테고리별 접이식 메뉴를 사이드바에 그린다.

    메뉴가 12개로 늘어나 한눈에 보기 힘들어져서, st.navigation의 기본 목록 UI는 숨기고
    (app.py의 position="hidden") 여기서 홈은 단독으로, 나머지는 st.expander로 묶어
    카테고리째로 접었다 펼 수 있게 직접 그린다. 각 링크를 st.container(key=...)로 감싸는 건
    현재 페이지 강조 때문 — st.page_link가 남기는 클래스명은 emotion이 빌드마다 새로 해시
    하므로, CSS만으로 "지금 보고 있는 페이지"를 안정적으로 고를 방법이 없다."""
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

        with st.container(key=_nav_key(home_page)):
            st.page_link(home_page, use_container_width=True)

        for title, pages in sections:
            with st.expander(title, expanded=True):
                for page in pages:
                    with st.container(key=_nav_key(page)):
                        st.page_link(page, use_container_width=True)

        st.html(
            f'<style>div[class*="st-key-{_nav_key(current_page)}"] '
            "a[data-testid=\"stPageLink-NavLink\"] {"
            "background: rgba(31, 111, 139, 0.28) !important;"
            "color: #8fd8e8 !important;"
            "font-weight: 800 !important;"
            "}</style>"
        )
