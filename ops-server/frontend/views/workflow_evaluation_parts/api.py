"""이 페이지 전용 백엔드 호출 도우미. 세션을 재사용해서(keep-alive) 매 요청마다
새 TCP 연결을 맺지 않는다(docs/local/PERF_OPS_EVAL_PAGE.md에서 확인한 최적화)."""

import requests
import streamlit as st

from config import OPS_BACKEND_URL

HTTP_TIMEOUT_SECONDS = 8
request_session = requests.Session()


def get_json(path: str, params: dict | None = None) -> tuple[object | None, str | None]:
    try:
        response = request_session.get(f"{OPS_BACKEND_URL}{path}", params=params or {}, timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json(), None
    except (requests.RequestException, ValueError) as error:
        return None, str(error)


def post_json(path: str, payload: dict) -> tuple[bool, str]:
    try:
        response = request_session.post(f"{OPS_BACKEND_URL}{path}", json=payload, timeout=HTTP_TIMEOUT_SECONDS)
        if response.status_code == 200:
            return True, ""
        return False, _read_error_message(response)
    except (requests.RequestException, ValueError) as error:
        return False, str(error)


def _read_error_message(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail or response.text)


def fetch_runs() -> list[dict]:
    if "workflow_eval_runs" not in st.session_state:
        data, error_message = get_json("/eval/runs")
        if error_message:
            st.error(f"평가 결과를 불러오지 못했습니다: {error_message}")
            data = []
        st.session_state["workflow_eval_runs"] = data
    return st.session_state["workflow_eval_runs"]


def clear_runs_cache() -> None:
    st.session_state.pop("workflow_eval_runs", None)


@st.fragment(run_every="2s")
def render_live_log(status_url_path: str, key: str) -> None:
    """실행 중인 평가의 진행 로그를 2초 간격으로 폴링해서 보여준다. Streamlit엔
    서버->브라우저 진짜 push 스트리밍이 없어서, 짧은 주기 자동 재실행으로
    "실시간처럼" 보이게 하는 현실적 타협이다. 이 폴링을 작은 fragment 하나로
    좁혀서, 다른 무거운 데이터(결과 테이블 등)까지 매번 다시 불러오는 걸 막는다."""
    status, error_message = get_json(status_url_path)
    if error_message:
        st.caption(f"진행 로그를 불러오지 못했습니다: {error_message}")
        return

    log_lines = status.get("log") or []
    if not log_lines and not status.get("running"):
        return
    running_suffix = " (실행 중...)" if status.get("running") else ""
    with st.expander(f"진행 로그{running_suffix}", expanded=bool(status.get("running"))):
        st.code("\n".join(log_lines[-100:]) or "(아직 로그 없음)", language="text")
