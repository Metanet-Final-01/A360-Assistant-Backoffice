import streamlit as st


def apply_global_styles() -> None:
    """페이지 전체에 적용할 최소한의 스타일: 여백을 살짝 넓히고, 제목 위 라벨(kicker) 색을 강조색으로."""
    st.markdown(
        """
        <style>
        .block-container {
            max-width: 1100px;
            padding-top: 3rem;
        }
        .app-kicker {
            color: var(--primary-color);
            font-size: 0.8rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            text-transform: uppercase;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(kicker: str, title: str) -> None:
    """제목 위에 작은 라벨을 붙여서 지금 어떤 페이지인지 한눈에 보이게 한다."""
    st.markdown(f'<div class="app-kicker">{kicker}</div>', unsafe_allow_html=True)
    st.title(title)
