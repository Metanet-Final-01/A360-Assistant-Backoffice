"""비용 리포트 뷰 — 사용자별·세션별 LLM 비용 (대시보드 #6).

메타넷 실무/운영에서 'AI가 얼마 썼나'를 설명하는 근거 화면. 백엔드가 group_by=user/session으로
집계해 주고(RPA-97 모델별 단가·RPA-124 세션 축), 여기서 표·차트로 보여준다.
"""

from datetime import datetime

import altair as alt
import pandas as pd
import requests
import streamlit as st

from components.layout import card, metric_strip, page_header, render_fetch_error, render_last_fetched, safe_api_get, section_header
from config import OPS_BACKEND_URL


def render() -> None:
    page_header("비용 리포트")
    st.caption(
        "사용자별·세션별 LLM 비용을 집계합니다. 아래 \"수집\"을 누르면 A360-Assistant-Backend에서 "
        "최근 기간 사용량을 축별로 가져옵니다. (세션별은 백엔드 group_by=session 반영 후 활성화됩니다.)"
    )

    with card("cost_controls"):
        col1, col2 = st.columns([1, 3])
        days = col2.slider("집계 기간(일)", 1, 90, 30, key="cost_days")
        if col1.button("수집", key="cost_collect", type="primary"):
            # 축별 수집 성공 여부를 상태코드로 기록 — 실패를 '미집계'로 삼키지 않는다(CodeRabbit #13).
            status: dict = {}
            for axis in ("user", "session"):
                try:
                    resp = requests.post(f"{OPS_BACKEND_URL}/observability/llm-usage/collect",
                                         params={"days": days, "group_by": axis}, timeout=15)
                    status[axis] = resp.status_code
                except requests.RequestException:
                    status[axis] = None  # 연결 실패
            st.session_state["cost_axis_status"] = status
            st.session_state["cost_fetched_at"] = datetime.now()

    render_last_fetched(st.session_state.get("cost_fetched_at"))

    status = st.session_state.get("cost_axis_status")
    if not status:
        st.info("\"수집\"을 눌러 사용량을 가져오세요.")
        return

    with card("cost_by_user"):
        section_header("사용자별 비용")
        _render_axis("user", "user_id", status.get("user"))

    with card("cost_by_session"):
        section_header("세션별 비용", "백엔드 group_by=session(RPA-124) 필요")
        _render_axis("session", "session_id", status.get("session"))


def _render_axis(axis: str, key_label: str, collect_status: int | None) -> None:
    # 수집 자체가 실패했으면 데이터를 그리지 않고 상태코드 기반 오류를 표시(원문 미노출).
    if collect_status is None:
        st.error(f"{axis} 축 수집 요청이 연결 실패했습니다 — 모니터링 백엔드가 켜져 있는지 확인하세요.")
        return
    if collect_status != 200:
        st.error(f"{axis} 축 수집 실패 (HTTP {collect_status}) — 백엔드가 group_by={axis}를 지원하는지·관리자 자격이 유효한지 확인하세요.")
        return
    # limit=1이 아니라 30 — 최신 스냅샷(snaps[0], 백엔드가 fetched_at 내림차순으로 준다)은
    # 지금까지처럼 요약·표에 쓰고, 나머지는 "이번 수집이 이전 수집들보다 늘었는지"에
    # 답하는 추세 차트에 쓴다.
    result = safe_api_get("/observability/llm-usage/snapshots", {"group_by": axis, "limit": 30})
    if not render_fetch_error(result, f"{axis} 축 조회"):
        return
    snaps = result.data
    if not snaps:
        st.info("해당 기간 사용량이 없습니다.")
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

    if len(snaps) >= 2:
        trend_df = pd.DataFrame([
            {"수집 시각": pd.to_datetime(s["fetched_at"]), "총 비용(USD)": s.get("total", {}).get("cost_usd", 0)}
            for s in snaps
        ]).sort_values("수집 시각")
        trend_chart = alt.Chart(trend_df).mark_line(point=True).encode(
            x=alt.X("수집 시각:T"),
            y=alt.Y("총 비용(USD):Q"),
            tooltip=["수집 시각:T", "총 비용(USD):Q"],
        ).properties(height=180)
        st.caption("이번 수집이 이전 수집들보다 비용이 늘었는지 보여줍니다.")
        st.altair_chart(trend_chart, use_container_width=True)
    else:
        st.caption("\"수집\"을 2회 이상 하면 이전 대비 비용 추세를 볼 수 있습니다.")

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
