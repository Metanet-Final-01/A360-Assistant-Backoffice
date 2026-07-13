"""비용 리포트 뷰 — 사용자별·세션별 LLM 비용 (대시보드 #6).

메타넷 실무/운영에서 'AI가 얼마 썼나'를 설명하는 근거 화면. 백엔드가 group_by=user/session으로
집계해 주고(RPA-97 모델별 단가·RPA-124 세션 축), 여기서 표·차트로 보여준다.
"""

import altair as alt
import pandas as pd
import requests
import streamlit as st

from components.layout import card, metric_strip, page_header, section_header
from config import OPS_BACKEND_URL


def render() -> None:
    page_header("COST", "비용 리포트")
    st.caption(
        "사용자별·세션별 LLM 비용을 집계합니다. 아래 \"수집\"을 누르면 A360-Assistant-Backend에서 "
        "최근 기간 사용량을 축별로 가져옵니다. (세션별은 백엔드 group_by=session 반영 후 활성화됩니다.)"
    )

    with card("cost_controls"):
        col1, col2 = st.columns([1, 3])
        days = col2.slider("집계 기간(일)", 1, 90, 30, key="cost_days")
        if col1.button("수집", key="cost_collect"):
            for axis in ("user", "session"):
                requests.post(f"{OPS_BACKEND_URL}/observability/llm-usage/collect",
                              params={"days": days, "group_by": axis}, timeout=15)
            st.session_state["cost_collected"] = True

    if not st.session_state.get("cost_collected"):
        st.info("\"수집\"을 눌러 사용량을 가져오세요.")
        return

    with card("cost_by_user"):
        section_header("사용자별 비용")
        _render_axis("user", "user_id")

    with card("cost_by_session"):
        section_header("세션별 비용", "백엔드 group_by=session(RPA-124) 필요")
        _render_axis("session", "session_id")


def _render_axis(axis: str, key_label: str) -> None:
    snaps = _safe_get(OPS_BACKEND_URL, "/observability/llm-usage/snapshots", {"group_by": axis, "limit": 1})
    if not snaps:
        st.warning(f"{axis} 축 집계가 아직 없습니다 — 백엔드가 group_by={axis}를 지원하는지(미지원 시 수집이 403/422로 실패) 확인하세요.")
        return
    snap = snaps[0]
    breakdown = snap.get("breakdown", [])
    if not breakdown:
        st.info("해당 기간 사용량이 없습니다.")
        return

    total = snap.get("total", {})
    metric_strip([
        ("총 비용", f"${total.get('cost_usd', 0):.4f}"),
        ("총 호출", f"{total.get('calls', 0):,}"),
        ("총 토큰", f"{total.get('input_tokens', 0) + total.get('output_tokens', 0):,}"),
        (f"{axis} 수", f"{len(breakdown)}"),
    ])

    df = pd.DataFrame(breakdown)
    df[key_label] = df["key"].fillna("(시스템)").astype(str).str[:12]
    df = df.rename(columns={"cost_usd": "cost_usd", "calls": "calls"}).sort_values("cost_usd", ascending=False)
    st.dataframe(
        df[[key_label, "calls", "input_tokens", "output_tokens", "cost_usd"]],
        use_container_width=True, hide_index=True,
    )
    top = df.head(10)
    chart = alt.Chart(top).mark_bar().encode(
        x=alt.X("cost_usd:Q", title="비용(USD)"),
        y=alt.Y(f"{key_label}:N", sort="-x", title=key_label),
        tooltip=[key_label, "cost_usd", "calls"],
    )
    st.altair_chart(chart, use_container_width=True)


def _safe_get(base_url: str, path: str, params: dict) -> list | dict | None:
    try:
        resp = requests.get(f"{base_url}{path}", params=params, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except requests.RequestException:
        return None
