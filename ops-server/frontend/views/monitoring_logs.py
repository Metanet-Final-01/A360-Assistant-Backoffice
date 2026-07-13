import pandas as pd
import requests
import streamlit as st

from components.layout import card, section_header
from components.monitoring.kpi_cards import render_kpi_cards
from components.monitoring.log_table import render_filters, render_table, render_table_controls
from components.monitoring.mock_data import generate_mock_logs
from components.monitoring.status_panel import render_latency_stats, render_status_distribution
from components.monitoring.styles import inject_dashboard_styles
from components.monitoring.volume_chart import render_volume_chart
from config import OPS_BACKEND_URL

# 감사 로그·LLM 사용량(원시) UI는 아직 없다 — A360-Assistant-Backend의 관리자 계정
# (ADMIN_EMAILS, RPA-109)이 준비되기 전까지는 백엔드(collect_audit_logs/collect_llm_usage
# 등, backend/app/main.py)만 만들어 두고 화면은 계정이 준비됐을 때 만든다. 아래
# metrics-daily/usage-daily/turn-events는 같은 관리자 인증을 쓰지만, RPA-109 설정이
# 끝나는 순간 바로 켜지도록 미리 붙여 둔다(RPA-110) — 코드는 이미 검증됨, 자격증명
# 미설정이면 403이 사람이 읽을 수 있는 메시지로 뜬다.


def render() -> None:
    inject_dashboard_styles()
    _render_top_bar()
    _render_log_dashboard()

    with st.expander("관리자 지표 (준비 중 — RPA-109 계정 설정 후 사용 가능)"):
        with card("obs_audit_logs"):
            section_header("감사 로그", "RPA-109 — 누가 무엇을 바꿨나, 관리자 계정 필요")
            _render_audit_logs()

        with card("obs_rag_events"):
            section_header(
                "RAG 파이프라인 단계 로그", "RPA-128 — embed/search/rerank 등 단계별 소요·설정, request_id로 조회, 관리자 계정 필요",
            )
            _render_rag_events()

        with card("obs_metrics_daily"):
            section_header("요청 성능 일별 롤업", "RPA-104 — 관리자 계정 설정(RPA-109) 필요")
            _render_metrics_daily()

        with card("obs_usage_daily"):
            section_header("LLM 사용량 일별 롤업", "RPA-104 — 관리자 계정 설정(RPA-109) 필요")
            _render_usage_daily()

        with card("obs_turn_events"):
            section_header("에이전트 턴 타임라인", "RPA-105 — session_id로 조회, 관리자 계정 필요")
            _render_turn_events()


def _render_top_bar() -> None:
    st.markdown(
        """
        <style>
        .obs-title { font-size: 1.6rem; font-weight: 800; color: #172026; line-height: 1.2; }
        .obs-title__sep { color: #98a6b0; font-weight: 500; margin: 0 6px; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    live = st.session_state.get("obs_log_live", False)

    col_title, col_deploy, col_kebab = st.columns([6, 1.1, 0.6])
    with col_title:
        st.markdown('<div class="obs-title">OBS<span class="obs-title__sep">/</span>모니터링 로그</div>', unsafe_allow_html=True)
    with col_deploy:
        if st.button("Deploy", key="obs_deploy_btn", width="stretch"):
            st.toast("배포 파이프라인은 아직 연동되지 않았습니다.", icon="🚧")
    with col_kebab:
        with st.popover("⋮", key="obs_kebab_btn"):
            st.caption("빠른 작업")
            if st.button("필터 초기화", key="obs_reset_filters_btn", width="stretch"):
                for key in ("obs_path_filter", "obs_method_filter", "obs_class_filter"):
                    st.session_state.pop(key, None)
                st.rerun()

    col_caption, col_live = st.columns([6, 1.7])
    with col_caption:
        st.caption("아래 대시보드는 현재 mock 데이터로 구성되어 있습니다 — 실제 백엔드 연동은 후속 작업입니다.")
    with col_live:
        label = "일시정지" if live else "실시간"
        # st.button 라벨은 순수 텍스트만 지원해 커스텀 SVG를 못 넣는다 — 아이콘은
        # emoji로 대체하고, 색상 대비용 SVG는 이후 배지 등에서만 raw HTML로 쓴다.
        if st.button(("⏸ " if live else "▶ ") + label, key="obs_live_btn", width="stretch"):
            st.session_state["obs_log_live"] = not live
            st.rerun()


def _render_delete_widget(source: str, filter_params: dict, state_key: str, key_prefix: str) -> None:
    """필터 조건(위에서 이미 고른 것)에 맞는 로컬 캐시 로그를 삭제한다 — Backend 원본
    관측 DB는 그대로, Ops가 가져온 사본만 지운다. 실수 방지로 확인 체크박스를 거친다."""
    active_filters = {k: v for k, v in filter_params.items() if v not in (None, "")}
    label = "필터 조건에 맞는 로그 삭제" if active_filters else "⚠ 전체 로그 삭제(필터 없음)"
    with st.expander(f"🗑 {label}"):
        if active_filters:
            st.caption(f"삭제 조건: {active_filters}")
        else:
            st.warning("현재 필터가 없어 이 소스의 로컬 로그 전체가 삭제됩니다.")
        confirmed = st.checkbox("정말 삭제하겠습니다", key=f"{key_prefix}_delete_confirm")
        if st.button("삭제 실행", key=f"{key_prefix}_delete_btn", disabled=not confirmed, type="primary"):
            try:
                resp = requests.delete(f"{OPS_BACKEND_URL}/observability/{source}", params=active_filters, timeout=15)
                resp.raise_for_status()
                st.session_state.pop(state_key, None)
                st.success(f"{resp.json().get('deleted', 0)}건 삭제했습니다.")
                st.rerun()
            except (requests.RequestException, ValueError) as e:
                st.error(f"삭제 실패: {e}")


def _render_audit_logs() -> None:
    col_btn, col_limit = st.columns([1, 3])
    limit = col_limit.number_input("최근 몇 건", min_value=10, max_value=2000, value=200, label_visibility="collapsed", key="audit_limit")
    if col_btn.button("새로고침", key="audit_refresh_btn") or "obs_audit_logs" not in st.session_state:
        _collect_and_load("audit-logs", {"limit": limit}, "obs_audit_logs")

    rows = st.session_state.get("obs_audit_logs", [])
    if not rows:
        st.info("아직 데이터가 없습니다 — 위 \"새로고침\"을 눌러 가져오세요(관리자 계정 필요).")
        return
    df = pd.DataFrame(rows)
    st.caption(f"{len(df)}건")

    with st.expander("필터"):
        cols = st.columns(3)
        method_filter = cols[0].selectbox("method", ["(전체)"] + sorted(df["method"].dropna().unique().tolist()), key="audit_method_filter")
        user_filter = cols[1].text_input("user_id", key="audit_user_filter")
        only_errors = cols[2].checkbox("에러(4xx/5xx)만", key="audit_only_errors")

    view = df
    if method_filter != "(전체)":
        view = view[view["method"] == method_filter]
    if user_filter:
        view = view[view["user_id"] == user_filter]
    if only_errors:
        view = view[view["status_code"] >= 400]
    st.dataframe(view[["created_at", "user_id", "method", "path", "status_code", "latency_ms"]], width="stretch", hide_index=True)

    _render_delete_widget(
        "audit-logs",
        {"method": method_filter if method_filter != "(전체)" else None, "user_id": user_filter or None},
        "obs_audit_logs", "audit_logs",
    )


def _render_log_dashboard() -> None:
    st.session_state.setdefault("obs_seed", 42)
    live = st.session_state.get("obs_log_live", False)

    # time.sleep()+st.rerun()으로 스크립트 전체를 되돌리는 대신, 이 구역만 fragment로 떼어내
    # live일 때만 주기적으로 재실행한다(evaluation.py의 _render_live_log와 동일 패턴) — KPI
    # 카드·차트·표까지 매번 통째로 다시 그리는 전체 재실행을 피한다.
    @st.fragment(run_every="4s" if live else None)
    def _dashboard_fragment() -> None:
        count = st.session_state.get("obs_log_limit", 100)
        seed = st.session_state["obs_seed"]
        df = generate_mock_logs(n=count, seed=seed)

        tab_dashboard, tab_table = st.tabs(["대시보드", "로그 테이블"])
        with tab_dashboard:
            render_kpi_cards(df)
            col_chart, col_side = st.columns([3, 2])
            with col_chart:
                render_volume_chart(df)
            with col_side:
                render_status_distribution(df)
                st.markdown('<div style="height:12px;"></div>', unsafe_allow_html=True)
                render_latency_stats(df)

        with tab_table:
            controls = render_table_controls()
            if controls["refresh_clicked"]:
                st.session_state["obs_seed"] += 1
                st.rerun()

            filtered = render_filters(df)
            render_table(filtered)

    _dashboard_fragment()


def _render_metrics_daily() -> None:
    col_btn, col_days = st.columns([1, 3])
    days = col_days.number_input("최근 며칠", min_value=1, max_value=90, value=7, key="metrics_daily_days", label_visibility="collapsed")
    if col_btn.button("새로고침", key="metrics_daily_refresh_btn") or "obs_metrics_daily" not in st.session_state:
        # GET /observability/metrics-daily는 days를 받지 않는다(method/path_contains/limit만) —
        # collect(수집 범위)와 get(조회)에 같은 params를 그대로 재사용하면 "최근 며칠" 입력이
        # 조회 결과엔 반영되지 않는다(CodeRabbit 지적). collect엔 days, get엔 limit을 따로 준다.
        _collect_and_load("metrics-daily", {"days": days}, "obs_metrics_daily", get_params={"limit": 2000})

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
        # metrics-daily와 같은 이유로 get엔 days 대신 limit을 준다.
        _collect_and_load("usage-daily", {"days": days}, "obs_usage_daily", get_params={"limit": 2000})

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

    _render_delete_widget("turn-events", {"session_id": session_id or None}, "obs_turn_events", "turn_events")


def _render_rag_events() -> None:
    col_btn, col_rid = st.columns([1, 3])
    request_id = col_rid.text_input("request_id (비우면 전체 최신순)", key="rag_events_request_id", label_visibility="collapsed")
    if col_btn.button("새로고침", key="rag_events_refresh_btn") or "obs_rag_events" not in st.session_state:
        params = {"request_id": request_id} if request_id else {}
        _collect_and_load("rag-events", params, "obs_rag_events")

    rows = st.session_state.get("obs_rag_events", [])
    if not rows:
        st.info("아직 데이터가 없습니다 — 위 \"새로고침\"을 눌러 가져오세요(관리자 계정 필요).")
        return
    df = pd.DataFrame(rows)
    st.caption(f"{len(df)}건 (event별: {', '.join(f'{k} {v}건' for k, v in df['event'].value_counts().items())})")
    st.dataframe(df[["created_at", "request_id", "event", "function", "status", "duration_ms"]], width="stretch", hide_index=True)

    _render_delete_widget("rag-events", {"request_id": request_id or None}, "obs_rag_events", "rag_events")


def _collect_and_load(source: str, collect_params: dict, state_key: str, get_params: dict | None = None) -> None:
    """collect_params는 수집(POST .../collect) 범위 지정용, get_params는 조회(GET) 필터용 —
    둘의 파라미터 셋이 다른 엔드포인트(metrics-daily/usage-daily의 days 등)가 있어 분리한다.
    get_params가 없으면 collect_params를 그대로 재사용한다."""
    try:
        collect_resp = requests.post(f"{OPS_BACKEND_URL}/observability/{source}/collect", params=collect_params, timeout=15)
        if collect_resp.status_code != 200:
            st.error(f"수집 실패: {collect_resp.text}")
            return
        resp = requests.get(f"{OPS_BACKEND_URL}/observability/{source}", params=get_params if get_params is not None else collect_params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            st.error(f"조회 응답 형식이 예상과 다릅니다: {data}")
            return
        st.session_state[state_key] = data
    except (requests.RequestException, ValueError) as e:
        st.error(f"백엔드 연결 실패: {e}")
