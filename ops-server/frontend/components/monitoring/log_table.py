import pandas as pd
import streamlit as st

from ..layout import card
from .theme import AMBER, GREEN, RED, STATUS_ORDER, status_class

_COUNT_MIN, _COUNT_MAX, _COUNT_STEP = 10, 500, 10


def render_table_controls() -> dict:
    """새로고침 버튼 / 표시 개수(- · +, 기본 100). Live는 상단 헤더 쪽 필 버튼이 담당한다."""
    st.session_state.setdefault("obs_log_limit", 100)
    limit = st.session_state["obs_log_limit"]

    with card("obs_controls"):
        col_refresh, col_minus, col_value, col_plus, col_caption = st.columns([1.4, 0.5, 0.7, 0.5, 3.2])
        refresh_clicked = col_refresh.button("새로고침", key="obs_refresh_btn", icon="🔄", width="stretch")
        if col_minus.button("−", key="obs_count_minus", width="stretch"):
            limit = max(_COUNT_MIN, limit - _COUNT_STEP)
            st.session_state["obs_log_limit"] = limit
        col_value.markdown(
            f'<div style="text-align:center;font-weight:800;font-family:Consolas,monospace;padding-top:8px;">{limit}</div>',
            unsafe_allow_html=True,
        )
        if col_plus.button("+", key="obs_count_plus", width="stretch"):
            limit = min(_COUNT_MAX, limit + _COUNT_STEP)
            st.session_state["obs_log_limit"] = limit
        col_caption.markdown(
            f'<div style="padding-top:10px;color:#8a94a0;font-size:0.82rem;">최근 {limit}건 '
            '(워크플로우 생성 호출은 "WF" 열에 표시)</div>',
            unsafe_allow_html=True,
        )

    return {"refresh_clicked": refresh_clicked, "limit": limit}


def render_filters(df: pd.DataFrame) -> pd.DataFrame:
    """접이식 필터 패널 — 경로 검색 / method 선택 / 상태 클래스(2xx·4xx·5xx) 토글."""
    with st.expander("필터", expanded=False):
        cols = st.columns([2, 1, 2])
        path_filter = cols[0].text_input(
            "경로 검색", key="obs_path_filter", placeholder="/api/sessions ..."
        )
        method_options = ["(전체)"] + sorted(df["method"].dropna().unique().tolist())
        method_filter = cols[1].selectbox("method", method_options, key="obs_method_filter")
        class_filter = cols[2].pills(
            "상태 클래스",
            STATUS_ORDER,
            selection_mode="multi",
            key="obs_class_filter",
        )

    view = df
    if path_filter:
        # regex=False — 경로에 정규식 메타문자(중괄호 등)가 섞여 있어도 안전하게 부분일치.
        view = view[view["path"].str.contains(path_filter, na=False, regex=False)]
    if method_filter != "(전체)":
        view = view[view["method"] == method_filter]
    if class_filter:
        view = view[view["status_code"].apply(status_class).isin(class_filter)]
    return view


def _inject_table_styles() -> None:
    st.markdown(
        """
        <style>
        div[data-testid="stDataFrame"] * {
            font-family: "Consolas", "SFMono-Regular", Menlo, monospace !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _status_badge_style(row: pd.Series) -> list[str]:
    color = {"2xx": GREEN, "4xx": AMBER, "5xx": RED}.get(status_class(row["status_code"]), "#667085")
    styles = [""] * len(row)
    styles[row.index.get_loc("status_code")] = (
        f"background-color:{color}; color:#ffffff; font-weight:700; border-radius:6px; text-align:center;"
    )
    return styles


def render_table(df: pd.DataFrame) -> None:
    """started_at / method / path / status_code / duration_ms / 워크플로우(WF) 컬럼.
    status_code는 상태 클래스별 배지 색상, 워크플로우는 체크(✓) 표시."""
    _inject_table_styles()
    if df.empty:
        st.info("조건에 맞는 로그가 없습니다.")
        return

    display_df = df.copy()
    display_df["started_at"] = display_df["started_at"].dt.strftime("%Y-%m-%d %H:%M:%S") + " UTC"
    display_df["워크플로우"] = display_df["워크플로우"].map({True: "✓", False: ""})

    st.caption(f"최근 {len(df)}건 · 시각은 UTC 기준")
    st.dataframe(
        display_df.style.apply(_status_badge_style, axis=1),
        width="stretch",
        hide_index=True,
        column_order=["started_at", "method", "path", "status_code", "duration_ms", "워크플로우"],
        column_config={"워크플로우": st.column_config.Column("WF")},
    )
