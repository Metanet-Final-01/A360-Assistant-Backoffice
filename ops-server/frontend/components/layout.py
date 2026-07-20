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
        /* 앱 전체의 단일 브랜드 색감 — section_header()의 틸→네이비 그라데이션을 기준으로
        삼아, 버튼·로고·사이드바 강조 등 "단색 청록"이 쓰이던 자리도 전부 이 톤으로 맞춘다. */
        :root {
            --brand-gradient: linear-gradient(135deg, #1f6f8b, #172026);
            --brand-teal: #1f6f8b;
        }
        .stApp {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic", "Pretendard", sans-serif;
        }
        .block-container {
            max-width: 1600px;
            padding: 2.5rem 3rem 3rem;
        }
        .page-subtitle {
            color: #667085;
            font-size: 1rem;
            margin: -0.55rem 0 1.2rem;
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
            border-radius: 16px;
            padding: 14px 16px;
        }
        .metric-strip__label { color: #667085; font-size: 0.78rem; font-weight: 700; }
        .metric-strip__value {
            color: #172026;
            font-size: 1.5rem;
            font-weight: 800;
            margin-top: 3px;
            font-family: "Consolas", "SFMono-Regular", Menlo, monospace;
            font-variant-numeric: tabular-nums;
        }
        .metric-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            grid-template-rows: repeat(2, 1fr);
            gap: 12px;
        }
        .metric-grid .metric-strip__item {
            min-height: 108px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }
        /* 홈 화면 상단 행 — 왼쪽 그래프 카드보다 오른쪽 2x2 지표 블록이 낮을 때, 그
        블록을 행 높이 기준 세로 가운데로 오게 한다(기본은 stretch라 위에 붙는다). */
        div[class*="st-key-home_top_row"] > div[data-testid="stHorizontalBlock"] {
            align-items: center;
        }
        div[data-testid="stButton"] button,
        div[data-testid="stFormSubmitButton"] button {
            border-radius: 9px;
            font-weight: 700;
        }
        /* 버튼은 그라데이션보다 단색이 더 깔끔해 보여서, section_header()의 그라데이션과
        같은 계열의 단색(--brand-teal)만 쓴다. */
        div[data-testid="stButton"] button[kind="primary"],
        div[data-testid="stFormSubmitButton"] button[kind="primary"] {
            background: var(--brand-teal) !important;
            border: none !important;
            color: #ffffff !important;
        }
        div[data-testid="stButton"] button[kind="primary"]:hover,
        div[data-testid="stFormSubmitButton"] button[kind="primary"]:hover {
            filter: brightness(1.12);
            color: #ffffff !important;
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
        폭도 프론트엔드(240px)에 맞춰 고정한다. ---------- */
        section[data-testid="stSidebar"] {
            width: 240px !important;
            min-width: 240px !important;
            max-width: 240px !important;
            background: linear-gradient(180deg, #1b2a33 0%, #172026 55%, #10181e 100%);
            border-right: 1px solid rgba(255, 255, 255, 0.06);
            position: relative;
        }

        /* st.navigation은 position="hidden"이라 자체 네비게이션 UI를 그리지 않고,
        render_sidebar()가 브랜드 행 + 카테고리 메뉴를 stSidebarUserContent 하나에 전부
        직접 그린다 — 예전처럼 stSidebarNav를 order로 끌어올릴 필요가 없다. stSidebarHeader는
        이제 로고를 그리지 않아(st.logo 미사용) 접기 화살표만 남기고, 로고 위 여백은 여기서
        직접 늘린다(브랜드 행 padding-top과 합쳐 로고 아래쪽 여백과 비슷한 크기로 맞춘다). */
        div[data-testid="stSidebarContent"] {
            display: flex;
            flex-direction: column;
        }

        section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] {
            height: 40px !important;
            min-height: 0;
            padding: 12px 8px 0;
            margin: 0 !important;
        }

        section[data-testid="stSidebar"] div[data-testid="stLogoSpacer"] {
            display: none !important;
        }

        section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] {
            padding: 0 !important;
            position: static !important;
        }

        .app-sidebar-brand {
            display: flex;
            align-items: center;
            justify-content: flex-start;
            gap: 10px;
            padding: 4px 12px 11px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            margin-bottom: 4px;
            line-height: 1;
        }

        .app-sidebar-brand__mark {
            flex-shrink: 0;
            width: 32px;
            height: 32px;
            background-repeat: no-repeat;
            background-size: contain;
        }

        .app-sidebar-brand__text {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }

        .app-sidebar-brand__title {
            font-weight: 800;
            font-size: 0.9rem;
            color: #e8f1f5;
            white-space: nowrap;
        }

        .app-sidebar-brand__subtitle {
            font-weight: 600;
            font-size: 0.7rem;
            color: #7d8f9b;
            white-space: nowrap;
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

        /* Streamlit이 사이드바 콘텐츠 전체에 기본으로 좌우 10px를 두는데, 그 안에 다시
        page_link·expander 자체 padding이 겹겹이 쌓여 메뉴 좌우 여백이 과하게 컸다 —
        세 겹을 전부 줄인다. */
        section[data-testid="stSidebar"] div[data-testid="stSidebarContent"] {
            padding: 0 6px 16px !important;
        }

        /* ---------- 카테고리 메뉴(render_sidebar) — st.navigation 기본 UI 대신 st.page_link +
        st.expander로 직접 그린 홈 링크·카테고리·하위 메뉴. 예전 stSidebarNavLink와 같은
        톤으로 맞춘다. ---------- */
        section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
            gap: 3px;
        }

        section[data-testid="stSidebar"] [data-testid="stPageLink"] {
            border-radius: 10px;
            font-weight: 700;
            font-size: 0.92rem;
        }

        section[data-testid="stSidebar"] [data-testid="stPageLink"] a {
            padding: 8px 10px;
            gap: 10px;
            color: #93a5b1 !important;
        }

        section[data-testid="stSidebar"] [data-testid="stPageLink"] a:hover {
            color: #e3edf2 !important;
            background: rgba(255, 255, 255, 0.06);
        }

        /* 현재 페이지 강조는 render_sidebar()가 st.container(key=...)로 감싼 항목을 골라
        st.html로 별도 <style>을 주입해 처리한다 — st.page_link가 남기는 emotion 클래스명은
        빌드마다 바뀌어 CSS만으로는 "현재 페이지"를 안정적으로 고를 수 없다. */

        section[data-testid="stSidebar"] [data-testid="stPageLink"] a span {
            color: inherit;
        }

        /* 카테고리(st.expander) — 얇은 소제목처럼 보이도록 기본 카드 테두리·배경을 지운다.
        summary/details 자체에 Streamlit이 배경·테두리를 직접 박아넣어서 래퍼뿐 아니라
        각 요소에 !important로 따로 지워야 한다. 카테고리 사이 구분은 details의 기본
        테두리 대신, 래퍼 위쪽에 얇은 선 하나만 남겨(border-top) 위 메뉴와 구분한다. */
        section[data-testid="stSidebar"] [data-testid="stExpander"] details {
            border: none !important;
            background: transparent !important;
        }

        section[data-testid="stSidebar"] [data-testid="stExpander"] {
            border: none !important;
            background: transparent !important;
            border-top: 1px solid rgba(255, 255, 255, 0.08) !important;
            margin-top: 10px;
            padding-top: 10px;
        }

        section[data-testid="stSidebar"] [data-testid="stExpander"] summary {
            padding: 6px 10px;
            border-radius: 8px;
            background: transparent !important;
            color: #7d8f9b !important;
            font-weight: 800;
            font-size: 0.72rem;
            letter-spacing: 0.04em;
        }

        section[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {
            color: #e3edf2 !important;
            background: rgba(255, 255, 255, 0.06) !important;
        }

        section[data-testid="stSidebar"] [data-testid="stExpander"] summary svg {
            fill: #7d8f9b;
        }

        section[data-testid="stSidebar"] [data-testid="stExpanderDetails"] {
            padding: 3px 0 0;
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
    st.title(title)
    if subtitle:
        st.markdown(f'<div class="page-subtitle">{subtitle}</div>', unsafe_allow_html=True)


def metric_strip(items: list[tuple[str, object]]) -> None:
    blocks = "".join(
        f'<div class="metric-strip__item"><div class="metric-strip__label">{label}</div>'
        f'<div class="metric-strip__value">{value}</div></div>'
        for label, value in items
    )
    st.markdown(f'<div class="metric-strip">{blocks}</div>', unsafe_allow_html=True)


def metric_grid(items: list[tuple[str, object]]) -> None:
    """metric_strip과 같은 타일이지만 2x2 고정 그리드 — 옆에 놓인 차트와 높이를 맞춘다."""
    blocks = "".join(
        f'<div class="metric-strip__item"><div class="metric-strip__label">{label}</div>'
        f'<div class="metric-strip__value">{value}</div></div>'
        for label, value in items
    )
    st.markdown(f'<div class="metric-grid">{blocks}</div>', unsafe_allow_html=True)


def card(key: str):
    """섹션을 흰 카드처럼 감싸는 컨테이너. 옅은 회색 페이지 배경 위에서 카드가 또렷하게 보이도록
    apply_global_styles()의 그림자 스타일과 짝을 이룬다. 사용법: with card("rag_ingest"): ..."""
    return st.container(border=True, key=f"card_{key}")
