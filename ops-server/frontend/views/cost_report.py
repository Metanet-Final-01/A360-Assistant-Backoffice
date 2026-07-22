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
    page_header("비용 리포트")
    st.caption(
        "사용자별·세션별 LLM 비용을 집계합니다. 관측 DB에서 직접 집계하므로 별도 수집 단계가 "
        "없습니다 — 기간을 고르고 \"조회\"를 누르면 그 시점의 집계를 바로 계산합니다."
    )

    with card("cost_controls"):
        col1, col2 = st.columns([1, 3])
        days = col2.slider("집계 기간(일)", 1, 90, 30, key="cost_days")
        if col1.button("조회", key="cost_collect", type="primary"):
            # 수집(POST .../collect) → 사본 저장 → 최신 1건 조회 3단이었는데, 화면이 쓰는 건
            # 결국 "지금 집계" 한 건뿐이라 직접 조회 한 번으로 줄였다(RPA-256). 사본은
            # 컨테이너 재시작마다 사라져 배포에서는 애초에 남지도 않았다.
            st.session_state["cost_days_queried"] = days

    days_queried = st.session_state.get("cost_days_queried")
    if not days_queried:
        st.info("기간을 고르고 \"조회\"를 누르세요.")
        return

    with card("cost_by_user"):
        section_header("사용자별 비용")
        _render_axis("user", "user_id", days_queried)

    with card("cost_by_session"):
        section_header("세션별 비용", "백엔드 group_by=session(RPA-124) 필요")
        _render_axis("session", "session_id", days_queried)


def _render_axis(axis: str, key_label: str, days: int) -> None:
    # order_by=cost — 이 화면은 '가장 비싼' 축을 보는 곳이다. 기본값(calls)으로 받으면
    # 호출은 적지만 비싼 세션이 상위 N에서 잘려 나가고, 화면은 받은 것만 정렬하므로
    # 그 사실조차 드러나지 않는다.
    snap = _safe_get(
        OPS_BACKEND_URL,
        "/observability/llm-usage/stats",
        {"group_by": axis, "days": days, "order_by": "cost"},
    )
    # 실패를 '사용량 없음'으로 삼키지 않는다 — 빈 화면과 조회 실패는 다른 상태다
    # (CodeRabbit #13에서 지적됐던 것과 같은 이유).
    if snap is None:
        st.error(
            f"{axis} 축 조회에 실패했습니다 — 모니터링 백엔드 상태와 관측 DB 직접 조회 구성"
            "(A360_OBSERVABILITY_DATABASE_URL)을 확인하세요."
        )
        return
    breakdown = snap.get("breakdown", [])
    if snap.get("breakdown_truncated"):
        # 합계는 전체인데 표는 상위 N개다 — 말하지 않으면 "표를 더하면 합계"라고 오해한다.
        st.caption(
            f"내역은 호출이 많은 상위 {len(breakdown)}개만 표시합니다"
            f" (전체 {snap.get('group_count', '?')}개). 합계는 전체 기준입니다."
        )
    if not breakdown:
        st.info("해당 기간 사용량이 없습니다.")
        return

    total = snap.get("total", {})
    metric_strip([
        ("총 비용", f"${total.get('cost_usd', 0):.4f}"),
        ("총 호출", f"{total.get('calls', 0):,}"),
        ("총 토큰", f"{total.get('input_tokens', 0) + total.get('output_tokens', 0):,}"),
        # 표시 행 수가 아니라 **전체 그룹 수**다 — 나머지 지표(총 비용·호출·토큰)가 전부
        # 전체 기준인데 여기만 잘린 행 수를 쓰면 같은 줄에서 숫자가 서로 어긋난다.
        (f"{axis} 수", f"{snap.get('group_count', len(breakdown)):,}"),
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
