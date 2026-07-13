import requests
import streamlit as st

from components.layout import card, metric_strip, page_header, section_header
from config import OPS_BACKEND_URL, RAG_SERVER_URL


def render() -> None:
    page_header(
        "HOME", "A360 Assistant Ops",
        "A360-Assistant-Backend 운영 도구 — RAG 데이터 적재, 워크플로우 평가, 백엔드 모니터링을 여기서 다룹니다.",
    )

    health = _get_health(OPS_BACKEND_URL)
    with card("home_status"):
        section_header("현재 상태")
        if health is None:
            st.error(f"모니터링 백엔드({OPS_BACKEND_URL})에 연결할 수 없습니다 — 서버가 켜져 있는지 확인하세요.")
        else:
            runs = _safe_get(OPS_BACKEND_URL, "/eval/runs") or []
            datasets = _safe_get(OPS_BACKEND_URL, "/eval/datasets") or []
            labels = sorted({r["agent_label"] for r in runs if r.get("agent_label")})
            rag_status = _safe_get(RAG_SERVER_URL, "/rag/ingest/status") or {}
            obs_status = _safe_get(OPS_BACKEND_URL, "/observability/status") or {}

            metric_strip([
                ("평가 로그", f"{len(runs)}건"),
                ("등록된 데이터셋", f"{len(datasets)}개"),
                ("비교 가능한 버전", f"{len(labels)}개"),
                ("RAG 적재 상태", "실행 중" if rag_status.get("running") else ("완료" if rag_status.get("returncode") == 0 else "-")),
            ])

            _render_backend_health_banner(obs_status.get("backend_health") or {})

            rag_logs_info = obs_status.get("rag_logs", {})
            last_collected = rag_logs_info.get("last_collected_at")
            st.caption(
                f"모니터링 로그 마지막 수집: {last_collected[:19].replace('T', ' ') if last_collected else '아직 없음'}"
            )

    with card("home_guide"):
        section_header("무엇을 할 수 있나요", "왼쪽 메뉴에서 아래 순서대로 이동하면 됩니다.")
        st.markdown(
            "1. **RAG 데이터 적재** — Automation 360 패키지/문서를 크롤링해 검색용 DB에 적재합니다(rag-server). "
            "여기서 적재한 데이터는 실서비스 백엔드에 그대로 반영됩니다.\n"
            "2. **평가** — 평가 데이터셋(case_id 목록)을 등록하고, agent 예측 결과를 pm4py/WorFBench로 "
            "채점해 자동 저장합니다. 기록된 로그는 같은 화면에서 목록 조회·버전 간 비교·Excel 내보내기까지 됩니다.\n"
            "3. **모니터링 로그** — A360-Assistant-Backend의 RAG 파이프라인 요청 로그(경로·상태·응답시간)를 "
            "가져와 조회합니다."
        )


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

    if st.button("백엔드 상태 새로고침", key="probe_backend_health"):
        result = _safe_get(OPS_BACKEND_URL, "/observability/backend-health?probe=true")
        if result is None:
            st.error("백엔드 상태 프로브 요청에 실패했습니다 — 모니터링 백엔드가 켜져 있는지 확인하세요.")
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
