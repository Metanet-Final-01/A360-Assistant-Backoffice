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

        </style>
        """,
        unsafe_allow_html=True,
    )


def section_header(text: str, description: str | None = None) -> None:
    """panel__header 톤(틸→네이비 그라데이션)의 섹션 라벨."""
    st.markdown(f'<div class="op-section-header">{text}</div>', unsafe_allow_html=True)
    if description:
        st.caption(description)


def page_header(kicker: str, title: str, subtitle: str | None = None) -> None:
    """제목 위에 작은 라벨을, 제목 아래에 브랜드 그라데이션 바를 붙여서 지금 어떤 페이지인지 한눈에 보이게 한다."""
    st.markdown(f'<div class="app-kicker">{kicker}</div>', unsafe_allow_html=True)
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
