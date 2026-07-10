import streamlit as st


def apply_global_styles() -> None:
    """페이지 전체에 적용할 스타일.
    - 한글이 또렷하게 보이도록 폰트를 시스템 한글 폰트 우선으로.
    - 제목 위 라벨(kicker) 색을 강조색으로, 제목 아래에 틸→네이비 그라데이션 바.
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
        .app-kicker {
            color: var(--primary-color);
            font-size: 0.8rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            text-transform: uppercase;
        }
        .app-accent-bar {
            height: 4px;
            width: 64px;
            border-radius: 999px;
            background: linear-gradient(135deg, #2f9ab2, #172026);
            margin: 0.3rem 0 1.2rem;
        }
        div[data-testid="stButton"] button,
        div[data-testid="stFormSubmitButton"] button {
            border-radius: 9px;
            font-weight: 700;
        }
        div[class*="st-key-card_"] {
            border-radius: 14px !important;
            box-shadow: 0 12px 28px rgba(23, 32, 38, 0.08);
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

        /* A360-Assistant-Frontend의 confidence-badge와 같은 배색 — badge()가 그린다. */
        .op-badge {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 999px;
            font-size: 0.72rem;
            font-weight: 800;
            white-space: nowrap;
        }
        .op-badge--success { color: #1f9d55; background: #e8f8ee; }
        .op-badge--mid { color: #2f6fa8; background: #eaf1fb; }
        .op-badge--warning { color: #b7791f; background: #fdf3e2; }
        .op-badge--danger { color: #d84a3a; background: #fbeceb; }
        .op-badge--neutral { color: #66727e; background: #eef2f6; }

        /* archive-chat__item(카드형 목록)과 톤을 맞춘 평가 결과 1건 표시 */
        .op-run-card__title { font-weight: 800; font-size: 0.92rem; color: #172026; }
        .op-run-card__meta { font-size: 0.78rem; color: #66727e; margin-top: 2px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


_BADGE_KINDS = {"success", "mid", "warning", "danger", "neutral"}


def badge(text: str, kind: str = "neutral") -> str:
    """A360-Assistant-Frontend의 confidence-badge와 같은 배색의 뱃지 HTML 조각을 만든다.
    st.markdown(..., unsafe_allow_html=True)로 렌더링해서 쓴다."""
    if kind not in _BADGE_KINDS:
        kind = "neutral"
    return f'<span class="op-badge op-badge--{kind}">{text}</span>'


def score_badge_kind(passed: bool | None, score: float | None) -> str:
    """pass/fail이 있으면 그걸 우선, 없으면 score 구간으로 판정한다."""
    if passed is True:
        return "success"
    if passed is False:
        return "danger"
    if score is None:
        return "neutral"
    if score >= 0.7:
        return "success"
    if score >= 0.4:
        return "mid"
    return "warning"


def section_header(text: str) -> None:
    """panel__header 톤(틸→네이비 그라데이션)의 섹션 라벨."""
    st.markdown(f'<div class="op-section-header">{text}</div>', unsafe_allow_html=True)


def page_header(kicker: str, title: str) -> None:
    """제목 위에 작은 라벨을, 제목 아래에 브랜드 그라데이션 바를 붙여서 지금 어떤 페이지인지 한눈에 보이게 한다."""
    st.markdown(f'<div class="app-kicker">{kicker}</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown('<div class="app-accent-bar"></div>', unsafe_allow_html=True)


def card(key: str):
    """섹션을 흰 카드처럼 감싸는 컨테이너. 옅은 회색 페이지 배경 위에서 카드가 또렷하게 보이도록
    apply_global_styles()의 그림자 스타일과 짝을 이룬다. 사용법: with card("rag_ingest"): ..."""
    return st.container(border=True, key=f"card_{key}")
