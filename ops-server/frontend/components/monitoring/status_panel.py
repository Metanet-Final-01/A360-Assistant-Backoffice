import pandas as pd
import streamlit as st

from ..layout import card
from .theme import status_color


def render_status_distribution(df: pd.DataFrame) -> None:
    """상태 코드 분포 — 실제 코드별(200/201/404/500 ...) 건수·비율 미터."""
    with card("obs_status_dist"):
        st.markdown('<div class="obs-card__title">상태 코드 분포</div>', unsafe_allow_html=True)
        st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)

        if df.empty:
            st.caption("데이터 없음")
            return

        total = len(df)
        counts = df["status_code"].value_counts().sort_values(ascending=False)

        rows = "".join(
            '<div class="obs-meter">'
            '<div class="obs-meter__top">'
            f'<span class="obs-meter__code">{code}</span>'
            f'<span class="obs-meter__stat">{count} · {count / total * 100:.1f}%</span>'
            "</div>"
            '<div class="obs-meter__track">'
            f'<div class="obs-meter__fill" style="width:{count / total * 100:.1f}%;background:{status_color(code)}"></div>'
            "</div>"
            "</div>"
            for code, count in counts.items()
        )
        st.markdown(rows, unsafe_allow_html=True)
        st.markdown('<div style="height:14px;"></div>', unsafe_allow_html=True)


def render_latency_stats(df: pd.DataFrame) -> None:
    """지연 통계(P50 / P95 / P99, ms)."""
    with card("obs_latency"):
        st.markdown('<div class="obs-card__title">응답 지연 (ms)</div>', unsafe_allow_html=True)
        st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)

        if df.empty or df["duration_ms"].isna().all():
            st.caption("데이터 없음")
            return

        p50 = df["duration_ms"].quantile(0.50)
        p95 = df["duration_ms"].quantile(0.95)
        p99 = df["duration_ms"].quantile(0.99)

        st.markdown(
            f"""
            <div class="obs-latency-row">
              <div class="obs-latency-item">
                <div class="obs-latency-item__label">P50</div>
                <div class="obs-latency-item__value">{p50:,.1f}</div>
              </div>
              <div class="obs-latency-item">
                <div class="obs-latency-item__label">P95</div>
                <div class="obs-latency-item__value">{p95:,.1f}</div>
              </div>
              <div class="obs-latency-item">
                <div class="obs-latency-item__label">P99</div>
                <div class="obs-latency-item__value">{p99:,.1f}</div>
              </div>
            </div>
            <div style="height:14px;"></div>
            """,
            unsafe_allow_html=True,
        )
