import altair as alt
import pandas as pd
import requests
import streamlit as st

from components.layout import card, page_header, section_header
from config import OPS_BACKEND_URL

# 워크플로우(Recommendation)를 만드는 호출 경로 표시용 — 아직 이 화면은 요청 메타데이터만
# 다루지만, 어떤 요청이 실제 워크플로우 생성으로 이어졌는지는 표시해 둔다.
_WORKFLOW_PATH_MARKER = "/turn"

# 감사 로그·LLM 사용량(원시) UI는 아직 없다 — A360-Assistant-Backend의 관리자 계정
# (ADMIN_EMAILS, RPA-109)이 준비되기 전까지는 백엔드(collect_audit_logs/collect_llm_usage
# 등, backend/app/main.py)만 만들어 두고 화면은 계정이 준비됐을 때 만든다. 아래
# metrics-daily/usage-daily/turn-events는 같은 관리자 인증을 쓰지만, RPA-109 설정이
# 끝나는 순간 바로 켜지도록 미리 붙여 둔다(RPA-110) — 코드는 이미 검증됨, 자격증명
# 미설정이면 403이 사람이 읽을 수 있는 메시지로 뜬다.


def render() -> None:
    page_header("OBS", "모니터링 로그")
    st.caption(
        "이렇게 쓰세요: 아래 \"새로고침\"을 누르면 A360-Assistant-Backend의 최근 요청 로그를 "
        "가져와 보여줍니다. 요청 메타데이터(경로·상태·응답시간)만 다루며, agent가 만든 실제 "
        "워크플로우 내용은 포함하지 않습니다."
    )

    with card("obs_rag_logs"):
        section_header("RAG 파이프라인 요청 로그")
        _render_refresh_and_table()

    with card("obs_metrics_daily"):
        section_header("요청 성능 일별 롤업", "RPA-104 — 관리자 계정 설정(RPA-109) 필요")
        _render_metrics_daily()

    with card("obs_usage_daily"):
        section_header("LLM 사용량 일별 롤업", "RPA-104 — 관리자 계정 설정(RPA-109) 필요")
        _render_usage_daily()

    with card("obs_turn_events"):
        section_header("에이전트 턴 타임라인", "RPA-105 — session_id로 조회, 관리자 계정 필요")
        _render_turn_events()


def _render_refresh_and_table() -> None:
    col_btn, col_limit = st.columns([1, 3])
    limit = col_limit.number_input("최근 몇 건", min_value=10, max_value=500, value=100, label_visibility="collapsed")
    if col_btn.button("새로고침", key="rag_refresh_btn") or "obs_rag_logs" not in st.session_state:
        _collect_and_load("rag-logs", {"limit": limit}, "obs_rag_logs")

    logs = st.session_state.get("obs_rag_logs", [])
    if not logs:
        st.info("아직 로그가 없습니다 — 위 \"새로고침\"을 눌러 가져오세요.")
        return

    df = _to_dataframe(logs)
    st.caption(f"최근 {len(df)}건 (워크플로우 생성 호출은 \"워크플로우\" 열이 ✓)")

    with st.expander("필터"):
        cols = st.columns(3)
        path_filter = cols[0].text_input("경로에 포함된 문자열", key="rag_path_filter")
        method_filter = cols[1].selectbox("method", ["(전체)"] + sorted(df["method"].dropna().unique().tolist()), key="rag_method_filter")
        only_errors = cols[2].checkbox("에러(4xx/5xx)만", key="rag_only_errors")

    view = df
    if path_filter:
        # regex=False — 그렇지 않으면 정규식 메타문자(괄호 등)가 섞인 경로를 입력했을 때
        # str.contains가 정규식으로 해석하다 re.error를 던져 페이지가 죽는다.
        view = view[view["path"].str.contains(path_filter, na=False, regex=False)]
    if method_filter != "(전체)":
        view = view[view["method"] == method_filter]
    if only_errors:
        view = view[view["status_code"] >= 400]

    st.dataframe(
        view[["started_at", "method", "path", "status_code", "duration_ms", "워크플로우"]],
        width="stretch",
        hide_index=True,
    )

    if view["duration_ms"].notna().any():
        chart = (
            alt.Chart(view)
            .mark_bar()
            .encode(
                x=alt.X("path:N", sort="-y", title="경로"),
                y=alt.Y("mean(duration_ms):Q", title="평균 응답시간(ms)"),
                color=alt.value("#2f9ab2"),
                tooltip=["path", "mean(duration_ms)"],
            )
            .properties(height=240)
        )
        st.altair_chart(chart, width="stretch")


def _collect_and_load(limit: int) -> None:
    try:
        collect_resp = requests.post(f"{OPS_BACKEND_URL}/observability/rag-logs/collect", params={"limit": limit}, timeout=15)
        if collect_resp.status_code != 200:
            st.error(f"로그 수집 실패: {collect_resp.text}")
            return
        resp = requests.get(f"{OPS_BACKEND_URL}/observability/rag-logs", params={"limit": limit}, timeout=10)
        st.session_state["obs_rag_logs"] = resp.json()
    except requests.RequestException as e:
        st.error(f"백엔드 연결 실패: {e}")


def _render_metrics_daily() -> None:
    col_btn, col_days = st.columns([1, 3])
    days = col_days.number_input("최근 며칠", min_value=1, max_value=90, value=7, key="metrics_daily_days", label_visibility="collapsed")
    if col_btn.button("새로고침", key="metrics_daily_refresh_btn") or "obs_metrics_daily" not in st.session_state:
        _collect_and_load("metrics-daily", {"days": days}, "obs_metrics_daily")

    rows = st.session_state.get("obs_metrics_daily", [])
    if not rows:
        st.info("아직 데이터가 없습니다 — 위 \"새로고침\"을 눌러 가져오세요(관리자 계정 필요).")
        return
    df = pd.DataFrame(rows)
    st.caption(f"{len(df)}행 (일자×method×path)")
    st.dataframe(df[["day", "method", "path", "calls", "err_4xx", "err_5xx", "p50_ms", "p95_ms", "avg_ms", "max_ms"]], width="stretch", hide_index=True)


def _render_usage_daily() -> None:
    col_btn, col_days = st.columns([1, 3])
    days = col_days.number_input("최근 며칠", min_value=1, max_value=365, value=30, key="usage_daily_days", label_visibility="collapsed")
    if col_btn.button("새로고침", key="usage_daily_refresh_btn") or "obs_usage_daily" not in st.session_state:
        _collect_and_load("usage-daily", {"days": days}, "obs_usage_daily")

    rows = st.session_state.get("obs_usage_daily", [])
    if not rows:
        st.info("아직 데이터가 없습니다 — 위 \"새로고침\"을 눌러 가져오세요(관리자 계정 필요).")
        return
    df = pd.DataFrame(rows)
    st.caption(f"{len(df)}행 (일자×component×purpose×model)")
    st.dataframe(df[["day", "component", "purpose", "model", "calls", "input_tokens", "output_tokens", "cost_usd"]], width="stretch", hide_index=True)


def _render_turn_events() -> None:
    col_btn, col_sid = st.columns([1, 3])
    session_id = col_sid.text_input("session_id (비우면 전체 최신순)", key="turn_events_session_id", label_visibility="collapsed")
    if col_btn.button("새로고침", key="turn_events_refresh_btn") or "obs_turn_events" not in st.session_state:
        params = {"session_id": session_id} if session_id else {}
        _collect_and_load("turn-events", params, "obs_turn_events")

    rows = st.session_state.get("obs_turn_events", [])
    if not rows:
        st.info("아직 데이터가 없습니다 — 위 \"새로고침\"을 눌러 가져오세요(관리자 계정 필요).")
        return
    df = pd.DataFrame(rows)
    st.caption(f"{len(df)}건")
    st.dataframe(df[["created_at", "session_id", "seq", "kind", "stage", "message", "elapsed_ms"]], width="stretch", hide_index=True)


def _collect_and_load(source: str, params: dict, state_key: str) -> None:
    try:
        collect_resp = requests.post(f"{OPS_BACKEND_URL}/observability/{source}/collect", params=params, timeout=15)
        if collect_resp.status_code != 200:
            st.error(f"수집 실패: {collect_resp.text}")
            return
        resp = requests.get(f"{OPS_BACKEND_URL}/observability/{source}", params=params, timeout=10)
        st.session_state[state_key] = resp.json()
    except requests.RequestException as e:
        st.error(f"백엔드 연결 실패: {e}")


def _to_dataframe(logs: list[dict]) -> pd.DataFrame:
    rows = []
    for r in logs:
        raw = r["raw"]
        path = raw.get("path") or ""
        rows.append(
            {
                "started_at": (raw.get("started_at") or "")[:19].replace("T", " "),
                "method": raw.get("method"),
                "path": path,
                "status_code": raw.get("status_code"),
                "duration_ms": raw.get("duration_ms"),
                "워크플로우": "✓" if _WORKFLOW_PATH_MARKER in path else "",
            }
        )
    return pd.DataFrame(rows)
