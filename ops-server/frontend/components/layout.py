import streamlit as st


def apply_global_styles() -> None:
    """페이지 전체에 적용할 스타일. A360-Assistant-Frontend(사용자용 앱)와 브랜드 톤(네이비·틸
    그라데이션)을 맞추되, 관리자용 도구임을 알 수 있도록 사이드바 뱃지 등에 앰버 포인트를 섞는다.
    - 한글이 또렷하게 보이도록 폰트를 시스템 한글 폰트 우선으로.
    - 사이드바는 프론트엔드 AppSidebar와 동일한 네이비 그라데이션 + 페이지 네비게이션 스타일.
    - card()로 감싼 영역은 옅은 회색 배경 위에 흰 카드+그림자로 떠 보이게(가시성 개선의 핵심)."""
    st.markdown(
        """
        <style>
        .stApp {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic", "Pretendard", sans-serif;
        }
        .block-container {
            max-width: 1600px;
            padding: 2.5rem 3rem 3rem;
        }
        .app-accent-bar {
            height: 4px;
            width: 64px;
            border-radius: 999px;
            background: linear-gradient(135deg, #2f9ab2, #172026);
            margin: 0.3rem 0 1.2rem;
        }
        .page-subtitle {
            color: #667085;
            font-size: 1rem;
            margin: -0.55rem 0 0.8rem;
        }
        .metric-strip {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 12px;
            margin: 0 0 1.2rem;
        }
        .metric-strip__item {
            background: #ffffff;
            border: 1px solid #e4e7ec;
            border-radius: 12px;
            padding: 14px 16px;
        }
        .metric-strip__label { color: #667085; font-size: 0.78rem; font-weight: 700; }
        .metric-strip__value { color: #172026; font-size: 1.45rem; font-weight: 800; margin-top: 3px; }
        div[data-testid="stButton"] button,
        div[data-testid="stFormSubmitButton"] button {
            border-radius: 9px;
            font-weight: 700;
        }
        div[class*="st-key-card_"] {
            border: 1px solid #dbe3ea !important;
            border-radius: 14px !important;
            box-shadow: 0 18px 40px rgba(23, 32, 38, 0.06);
        }

        /* A360-Assistant-Frontend의 panel__header(틸→네이비 그라데이션)와 톤을 맞춘
        섹션 라벨 — section_header()가 그린다. */
        .op-section-header {
            background: linear-gradient(135deg, #1f6f8b, #172026);
            color: #ffffff;
            padding: 10px 16px;
            border-radius: 10px;
            font-weight: 800;
            font-size: 0.92rem;
            margin-bottom: 0.9rem;
        }

        /* ---------- 상단 헤더 바 — 관리자 도구에는 필요 없어 시각적으로 제거한다. 다만 사이드바를
        접었을 때 다시 펼치는 화살표(stExpandSidebarButton)가 이 안에서만 렌더링되므로 DOM에서
        완전히 지우지는 않고 투명하게 비워, 그 경우에만 화살표가 뜨도록 남겨둔다. ---------- */
        header[data-testid="stHeader"] {
            background: transparent;
            box-shadow: none;
        }

        div[data-testid="stAppDeployButton"],
        [data-testid="stMainMenuButton"],
        div[data-testid="stToolbarActions"],
        div[data-testid="stStatusWidget"] {
            display: none !important;
        }

        /* ---------- 사이드바(메뉴바) — 프론트엔드 app-sidebar와 동일한 네이비 그라데이션.
        폭도 프론트엔드(240px)에 맞춰 고정한다. 관리자용 도구임을 표시하려고 로고 옆
        OPS 뱃지(앰버)만 톤을 다르게 둔다. ---------- */
        section[data-testid="stSidebar"] {
            width: 240px !important;
            min-width: 240px !important;
            max-width: 240px !important;
            background: linear-gradient(180deg, #1b2a33 0%, #172026 55%, #10181e 100%);
            border-right: 1px solid rgba(255, 255, 255, 0.06);
        }

        /* st.navigation은 항상 stSidebarHeader 바로 아래에 그려지고, render_sidebar()로 추가한
        내용(stSidebarUserContent — 로고+타이틀+뱃지 한 줄)은 원래 그 아래(네비게이션보다 더
        아래)에 붙는다. 브랜드 행이 네비게이션보다 위에 오도록 flex order로 시각적 순서만
        바꾼다. stSidebarHeader는 이제 로고를 그리지 않아(st.logo 미사용) 접기 화살표만
        남기고 여백을 최소로 줄인다. */
        div[data-testid="stSidebarContent"] {
            display: flex;
            flex-direction: column;
        }

        /* 헤더 자체가 콘텐츠와 무관하게 고정 높이(60px)를 가지고 있어서 padding/min-height만으로는
        안 줄어든다 — 로고 자리 예약분(stLogoSpacer, 32px)을 없애고 접기 버튼(28px) + 위쪽
        여백(6px)만 남도록 높이도 직접 지정한다. */
        section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] {
            order: 0;
            height: 34px !important;
            min-height: 0;
            padding: 6px 8px 0;
            margin: 0 !important;
        }

        section[data-testid="stSidebar"] div[data-testid="stLogoSpacer"] {
            display: none !important;
        }

        /* Streamlit이 이 블록에 기본으로 padding-bottom(약 96px)을 붙이는데, 브랜드 행 하나만
        들어있는 지금은 그게 고스란히 네비게이션 위 빈 여백이 되어 전부 0으로 지운다. */
        section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] {
            order: 1;
            padding: 0 !important;
        }

        section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] {
            order: 2;
        }

        .app-sidebar-brand {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 4px 12px 16px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            margin-bottom: 6px;
            line-height: 1;
        }

        .app-sidebar-brand__mark {
            flex-shrink: 0;
            width: 28px;
            height: 28px;
            background-repeat: no-repeat;
            background-size: contain;
        }

        .app-sidebar-brand__title {
            font-weight: 800;
            font-size: 0.86rem;
            letter-spacing: 0.06em;
            color: #e8f1f5;
            white-space: nowrap;
        }

        .app-sidebar-brand__badge {
            flex-shrink: 0;
            font-size: 0.62rem;
            font-weight: 800;
            letter-spacing: 0.03em;
            color: #ffffff;
            background: #b7791f;
            padding: 2px 8px;
            border-radius: 999px;
        }

        section[data-testid="stSidebar"] div[data-testid="stSidebarCollapseButton"] button,
        button[data-testid="stExpandSidebarButton"] {
            color: #93a5b1;
        }

        section[data-testid="stSidebar"] div[data-testid="stSidebarCollapseButton"] button:hover,
        button[data-testid="stExpandSidebarButton"]:hover {
            color: #e3edf2;
            background: rgba(255, 255, 255, 0.08);
        }

        section[data-testid="stSidebar"] [data-testid="stSidebarNavItems"] {
            padding: 10px 12px;
            gap: 3px;
            display: flex;
            flex-direction: column;
        }

        section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] {
            border-radius: 10px;
            padding: 10px 12px;
            font-weight: 700;
            font-size: 0.92rem;
            color: #93a5b1;
            gap: 10px;
        }

        section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"]:hover {
            color: #e3edf2;
            background: rgba(255, 255, 255, 0.06);
        }

        section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"][aria-current="page"] {
            color: #8fd8e8;
            background: rgba(47, 154, 178, 0.18);
            font-weight: 800;
        }

        section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] span {
            color: inherit;
        }

        section[data-testid="stSidebar"] div[data-testid="stSidebarNavSeparator"] {
            border-color: rgba(255, 255, 255, 0.08);
            margin: 8px 20px;
        }

        section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] p,
        section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] small,
        section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
            color: #7d8f9b !important;
        }

        </style>
        """,
        unsafe_allow_html=True,
    )


def section_header(text: str, description: str | None = None) -> None:
    """panel__header 톤(틸→네이비 그라데이션)의 섹션 라벨."""
    st.markdown(f'<div class="op-section-header">{text}</div>', unsafe_allow_html=True)
    if description:
        st.caption(description)


def page_header(title: str, subtitle: str | None = None) -> None:
    """제목 아래에 브랜드 그라데이션 바를 붙여서 지금 어떤 페이지인지 한눈에 보이게 한다."""
    st.title(title)
    if subtitle:
        st.markdown(f'<div class="page-subtitle">{subtitle}</div>', unsafe_allow_html=True)
    st.markdown('<div class="app-accent-bar"></div>', unsafe_allow_html=True)


def metric_strip(items: list[tuple[str, object]]) -> None:
    blocks = "".join(
        f'<div class="metric-strip__item"><div class="metric-strip__label">{label}</div>'
        f'<div class="metric-strip__value">{value}</div></div>'
        for label, value in items
    )
    st.markdown(f'<div class="metric-strip">{blocks}</div>', unsafe_allow_html=True)


def card(key: str):
    """섹션을 흰 카드처럼 감싸는 컨테이너. 옅은 회색 페이지 배경 위에서 카드가 또렷하게 보이도록
    apply_global_styles()의 그림자 스타일과 짝을 이룬다. 사용법: with card("rag_ingest"): ..."""
    return st.container(border=True, key=f"card_{key}")
