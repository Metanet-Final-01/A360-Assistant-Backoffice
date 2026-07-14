import pandas as pd
import streamlit as st

from .icons import icon_activity, icon_alert_triangle, icon_share, icon_timer
from .theme import AMBER, GREEN, RED, TEAL


def render_kpi_cards(df: pd.DataFrame) -> None:
    """① 총 요청 ② 에러율(5xx) ③ P95 응답시간 ④ 워크플로우 호출 — 4열 KPI 카드."""
    total = len(df)
    err_5xx = int((df["status_code"] >= 500).sum()) if total else 0
    err_rate = (err_5xx / total * 100) if total else 0.0
    p95 = df["duration_ms"].quantile(0.95) if total else 0.0
    workflow_calls = int(df["워크플로우"].sum()) if total else 0

    err_color = RED if err_rate >= 5 else (AMBER if err_rate >= 1 else GREEN)
    p95_color = RED if p95 >= 1000 else (AMBER if p95 >= 400 else GREEN)

    cards = [
        ("총 요청", f"{total:,}", "건", "현재 로딩된 로그 기준", icon_activity(18, TEAL)),
        ("에러율", f"{err_rate:.2f}", "%", "5xx 응답 비율", icon_alert_triangle(18, err_color)),
        ("P95 응답시간", f"{p95:,.1f}", "ms", "상위 5% 지연", icon_timer(18, p95_color)),
        ("워크플로우 호출", f"{workflow_calls:,}", "건", "워크플로우 생성 요청", icon_share(18, TEAL)),
    ]
    blocks = "".join(
        '<div class="obs-card">'
        '<div class="obs-kpi-card__top">'
        f'<span class="obs-kpi-card__label">{label}</span>'
        f'<span class="obs-kpi-card__icon">{icon}</span>'
        "</div>"
        f'<div class="obs-kpi-card__value">{value}<span class="obs-kpi-card__unit">{unit}</span></div>'
        f'<div class="obs-kpi-card__sub">{sub}</div>'
        "</div>"
        for label, value, unit, sub, icon in cards
    )
    st.markdown(f'<div class="obs-kpi-row">{blocks}</div>', unsafe_allow_html=True)
