import altair as alt
import pandas as pd
import streamlit as st

from ..layout import card
from .theme import AMBER, GREEN, RED, STATUS_ORDER, status_class

_LEGEND = "".join(
    f'<span class="obs-legend__item"><span class="obs-legend__dot" style="background:{color}"></span>{cls}</span>'
    for cls, color in zip(STATUS_ORDER, [GREEN, AMBER, RED])
)


def render_volume_chart(df: pd.DataFrame) -> None:
    """30초 버킷 단위 누적 막대 — 상태 코드 클래스별 색상 스택(2xx=초록/4xx=앰버/5xx=빨강)."""
    with card("obs_volume"):
        col_title, col_legend = st.columns([3, 2])
        with col_title:
            st.markdown(
                '<div class="obs-card__title">시간대별 요청량</div>'
                '<div class="obs-card__subtitle">30초 단위 · 상태 코드별 누적</div>',
                unsafe_allow_html=True,
            )
        with col_legend:
            st.markdown(
                f'<div class="obs-legend" style="justify-content:flex-end;margin-top:6px;">{_LEGEND}</div>',
                unsafe_allow_html=True,
            )

        if df.empty:
            st.caption("표시할 로그가 없습니다.")
            return

        view = df.copy()
        view["status_class"] = view["status_code"].apply(status_class)
        view["bucket"] = view["started_at"].dt.floor("30s")

        grouped = view.groupby(["bucket", "status_class"], as_index=False).size()
        grouped = grouped.rename(columns={"size": "count"})

        x_min = grouped["bucket"].min()
        x_max = grouped["bucket"].max()

        chart = (
            alt.Chart(grouped)
            .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, stroke="#ffffff", strokeWidth=1)
            .encode(
                x=alt.X(
                    "bucket:T",
                    title=None,
                    axis=alt.Axis(
                        values=[x_min, x_max],
                        format="%H:%M:%S",
                        grid=False,
                        domain=False,
                        ticks=False,
                        labelColor="#8a94a0",
                    ),
                ),
                y=alt.Y("count:Q", title=None, stack="zero", axis=None),
                color=alt.Color(
                    "status_class:N",
                    sort=STATUS_ORDER,
                    scale=alt.Scale(domain=STATUS_ORDER, range=[GREEN, AMBER, RED]),
                    legend=None,
                ),
                order=alt.Order("status_class:N", sort="ascending"),
                tooltip=[
                    alt.Tooltip("bucket:T", title="시각(UTC)", format="%H:%M:%S"),
                    alt.Tooltip("status_class:N", title="상태"),
                    alt.Tooltip("count:Q", title="건수"),
                ],
            )
            .configure_view(strokeWidth=0)
            .properties(height=260)
        )
        st.altair_chart(chart, width="stretch")
