from dataclasses import dataclass
from datetime import datetime

import requests
import streamlit as st

from config import OPS_BACKEND_URL


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
        폭도 프론트엔드(240px)에 맞춰 고정한다. 관리자용 도구임을 표시하려고 로고 옆
        OPS 뱃지(앰버)만 톤을 다르게 둔다. ---------- */
        section[data-testid="stSidebar"] {
            width: 240px !important;
            min-width: 240px !important;
            max-width: 240px !important;
            background: linear-gradient(180deg, #1b2a33 0%, #172026 55%, #10181e 100%);
            border-right: 1px solid rgba(255, 255, 255, 0.06);
            position: relative;
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
            justify-content: flex-start;
            gap: 10px;
            padding: 4px 12px 16px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            margin-bottom: 6px;
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
        stSidebarNavItems·링크 자체 padding이 겹겹이 쌓여 메뉴 좌우 여백이 과하게 컸다 —
        세 겹을 전부 줄인다. */
        section[data-testid="stSidebar"] div[data-testid="stSidebarContent"] {
            padding: 0 6px !important;
        }

        section[data-testid="stSidebar"] [data-testid="stSidebarNavItems"] {
            padding: 6px 4px;
            gap: 3px;
            display: flex;
            flex-direction: column;
        }

        section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] {
            border-radius: 10px;
            padding: 10px 10px;
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
            background: rgba(31, 111, 139, 0.28);
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


@dataclass
class ApiResult:
    """`safe_api_get` 응답 — 5개 관측 화면(trace/cost_report/loadtest/log_eda/
    runtime_settings)이 각자 따로 짜뒀던 GET+에러처리를 하나로 합친 것(2026-07-20)."""

    data: object | None
    error_message: str | None
    status_code: int | None


def safe_api_get(path: str, params: dict | None = None, timeout: int = 10) -> ApiResult:
    """OPS_BACKEND_URL 기준 GET. 연결 실패/파싱 실패/비200 상태를 전부 ApiResult 하나로
    돌려준다 — 호출부는 requests.RequestException을 직접 잡을 필요가 없다."""
    try:
        resp = requests.get(f"{OPS_BACKEND_URL}{path}", params=params or {}, timeout=timeout)
    except requests.RequestException as e:
        return ApiResult(None, f"연결하지 못했습니다: {e}", None)
    if resp.status_code != 200:
        return ApiResult(None, resp.text, resp.status_code)
    try:
        return ApiResult(resp.json(), None, 200)
    except ValueError as e:
        return ApiResult(None, f"응답 파싱 실패: {e}", 200)


def render_fetch_error(result: ApiResult, context: str) -> bool:
    """ApiResult를 상태코드별로 안내하고, 화면을 계속 그려도 되면 True를 돌려준다.
    (runtime_settings.py의 403/502 구분 문구를 5개 화면 공통 규칙으로 승격)."""
    if result.status_code == 403:
        st.error(f"{context} 실패 — 권한이 없습니다.\n\n{result.error_message}")
        return False
    if result.status_code == 502:
        st.error(f"{context} 실패 — 백엔드에 연결하지 못했습니다.\n\n{result.error_message}")
        return False
    if result.error_message:
        st.error(f"{context} 실패: {result.error_message}")
        return False
    return True


def render_last_fetched(fetched_at: datetime | None) -> None:
    """"이 표가 방금 것인지 모른다"는 문제를 없애는 최소 표시 — 마지막 조회 시각 한 줄."""
    if fetched_at is None:
        st.caption("아직 조회하지 않음")
    else:
        st.caption(f"마지막 조회: {fetched_at.strftime('%H:%M:%S')}")
