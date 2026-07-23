from concurrent.futures import ThreadPoolExecutor

import altair as alt
import pandas as pd
import requests
import streamlit as st

from components.layout import card, metric_grid, page_header, section_header
from config import OPS_BACKEND_URL, RAG_SERVER_URL


def render() -> None:
    page_header(
        "A360 Assistant Ops",
        "A360-Assistant-Backend 운영 도구 — RAG 데이터 적재, 워크플로우 평가, 백엔드 모니터링을 여기서 다룹니다.",
    )

    health = _get_health(OPS_BACKEND_URL)
    if health is None:
        st.error(f"모니터링 백엔드({OPS_BACKEND_URL})에 연결할 수 없습니다 — 서버가 켜져 있는지 확인하세요.")
        return

    # 아래 5개 요청은 서로 의존성이 없다 — 순차로 부르면(예전 방식) 각자 최대 5초 타임아웃이
    # 그대로 누적돼 백엔드 하나만 느려져도 최초 화면 렌더가 수 초씩 밀린다. 병렬로 쏘고
    # 가장 느린 하나의 시간만큼만 기다리게 한다.
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_runs = pool.submit(_safe_get, OPS_BACKEND_URL, "/eval/runs")
        f_datasets = pool.submit(_safe_get, OPS_BACKEND_URL, "/eval/datasets")
        f_rag_status = pool.submit(_safe_get, RAG_SERVER_URL, "/rag/ingest/status")
        f_obs_status = pool.submit(_safe_get, OPS_BACKEND_URL, "/observability/status")
        f_rag_logs = pool.submit(_safe_get, OPS_BACKEND_URL, "/observability/rag-logs")

        runs = f_runs.result() or []
        datasets = f_datasets.result() or []
        rag_status = f_rag_status.result() or {}
        obs_status = f_obs_status.result() or {}
        # `or []`로 뭉개지 않는다 — 조회 실패(예: 관측 DB 미구성 503)와 "로그가 없음"은
        # 다른 상태다. 실패를 빈 데이터로 바꾸면 미구성·장애가 '표시할 로그 없음'으로 보여
        # 아무도 눈치채지 못한다(직접 조회로 503을 드러내기로 한 결정과 정면으로 어긋난다).
        rag_logs = f_rag_logs.result()
    labels = sorted({r["agent_label"] for r in runs if r.get("agent_label")})

    # 차트(그래프 전용 카드)와 2x2 지표 카드를 거의 1:1 너비로 나란히 둔다 — 기존엔
    # 차트:지표 = 3:2였고 차트+지표+백엔드 상태가 카드 하나를 같이 썼는데, 지표 카드가
    # 상대적으로 좁아 보이고 백엔드 상태가 다른 성격의 정보와 섞여 있었다. 2x2 지표는
    # 그 자체로 각각 카드형이라 바깥에 카드를 한 겹 더 두르지 않는다(이중 카드 방지).
    with st.container(key="home_top_row"):
        col_chart, col_metrics = st.columns([1, 1])
        with col_chart:
            with card("home_chart"):
                _render_recent_logs_chart(rag_logs)
        with col_metrics:
            metric_grid([
                ("평가 로그", f"{len(runs)}건"),
                ("등록된 데이터셋", f"{len(datasets)}개"),
                ("비교 가능한 버전", f"{len(labels)}개"),
                ("RAG 적재 상태", "실행 중" if rag_status.get("running") else ("완료" if rag_status.get("returncode") == 0 else "-")),
            ])

    with card("home_backend_status"):
        _render_backend_health_banner(obs_status.get("backend_health") or {})
        # 예전엔 collector의 '마지막 수집 시각'을 보여줬는데, 이제 rag-logs는 관측 DB를
        # 직접 읽으므로 그 시각은 화면 데이터의 최신성과 아무 상관이 없다(수집 자체를 안 한다).
        # 운영자가 "언제까지의 데이터인가"를 오해하지 않도록, 실제로 받아온 로그의 최신
        # 시각을 보여준다.
        latest = max(
            (log.get("created_at") for log in (rag_logs or []) if log.get("created_at")),
            default=None,
        )
        st.caption(
            f"RAG 요청 로그 최신 기록: {latest[:19].replace('T', ' ') if latest else '없음'}"
            " (관측 DB 직접 조회)"
        )


def _render_recent_logs_chart(rag_logs: list[dict] | None) -> None:
    """RAG 파이프라인 요청의 최근 응답시간 추이 — 단일 시계열이라 범례 없이 직관적으로 보여준다."""
    section_header("RAG 요청 응답시간 추이")
    if rag_logs is None:
        # 조회 실패를 빈 차트로 그리면 "요청이 없었다"로 읽힌다 — 실패는 실패로 보여준다.
        st.error(
            "RAG 요청 로그를 불러오지 못했습니다 — 모니터링 백엔드 상태와 관측 DB 직접 조회"
            " 구성(A360_OBSERVABILITY_DATABASE_URL)을 확인하세요."
        )
        return
    # rag_events(event='http_request')를 직접 읽으므로 raw dict가 아니라 정형 컬럼이다.
    rows = [
        {
            "started_at": log.get("created_at"),
            "duration_ms": log.get("duration_ms"),
        }
        for log in rag_logs
        if log.get("duration_ms") is not None and log.get("created_at")
    ]
    if not rows:
        st.info("표시할 RAG 요청 로그가 없습니다 — 관측 DB에 http_request 이벤트가 있는지 확인하세요.")
        return

    df = pd.DataFrame(rows).sort_values("started_at").tail(50)
    chart = (
        alt.Chart(df)
        .mark_line(point=True, color="#1f6f8b", strokeWidth=2)
        .encode(
            x=alt.X("started_at:T", title="시각"),
            y=alt.Y("duration_ms:Q", title="응답시간(ms)"),
            tooltip=["started_at", "duration_ms"],
        )
        .properties(height=300)
    )
    st.altair_chart(chart, width="stretch")


def _fmt_ts(ts: str | None) -> str:
    return ts[:19].replace("T", " ") if ts else "-"


def _render_backend_health_banner(health: dict) -> None:
    """A360-Assistant-Backend 생존 상태 배너 — 데이터 수집(로그인)과 분리된 무인증 프로브 결과.

    캐시된 상태를 보여주고, 버튼으로 지금 다시 프로브한다. 백엔드가 죽으면 '조회'가
    아니라 이 배너로 '죽었다는 사실'을 드러내는 게 목적이다.
    """
    status = (health or {}).get("status", "unknown")
    checked_at = _fmt_ts((health or {}).get("checked_at"))
    last_ok = _fmt_ts((health or {}).get("last_ok_at"))

    if status == "healthy":
        st.success(f"🟢 백엔드 UP (healthy) · 확인 {checked_at}")
    elif status == "degraded":
        st.warning(f"🟡 백엔드 UP·성능저하 (degraded — 관측 DB 등 일부 이상) · 확인 {checked_at}")
    elif status in ("unhealthy", "unreachable"):
        st.error(f"🔴 백엔드 DOWN ({status}) · 마지막 정상 {last_ok} · 확인 {checked_at}")
    else:
        st.info("⚪ 백엔드 상태 미확인 — 아래 버튼으로 확인하세요.")

    if st.button("백엔드 상태 새로고침", key="probe_backend_health", type="primary"):
        result = _safe_get(OPS_BACKEND_URL, "/observability/backend-health?probe=true")
        if result is None:
            st.error("백엔드 상태 프로브 요청에 실패했습니다 — 모니터링 백엔드가 켜져 있는지 확인하세요.")
        else:
            st.rerun()


def _get_health(base_url: str) -> dict | None:
    try:
        resp = requests.get(f"{base_url}/health", timeout=5)
        return resp.json() if resp.status_code == 200 else None
    except requests.RequestException:
        return None


def _safe_get(base_url: str, path: str) -> list | dict | None:
    try:
        resp = requests.get(f"{base_url}{path}", timeout=5)
        return resp.json() if resp.status_code == 200 else None
    except requests.RequestException:
        return None
