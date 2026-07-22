"""이 페이지 전용 백엔드 호출 도우미."""

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


def post_json(path: str, payload: dict) -> tuple[dict | None, str | None]:
    try:
        response = request_session.post(f"{OPS_BACKEND_URL}{path}", json=payload, timeout=HTTP_TIMEOUT_SECONDS)
        if response.status_code == 200:
            return response.json(), None
        return None, _read_error_message(response)
    except (requests.RequestException, ValueError) as error:
        return None, str(error)


def _read_error_message(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail or response.text)


def fetch_rules() -> list[dict]:
    rules, error_message = get_json("/eval/workflow/action-rules")
    if error_message:
        st.warning(f"규칙 목록을 불러오지 못했습니다: {error_message}")
        return []
    return rules or []


def fetch_current_version() -> int | None:
    data, error_message = get_json("/eval/workflow/action-rules/version")
    if error_message:
        return None
    return data.get("current_version") if data else None


def fetch_events(limit: int = 100) -> list[dict]:
    events, error_message = get_json("/eval/workflow/action-rules/events", {"limit": limit})
    if error_message:
        st.warning(f"변경 이력을 불러오지 못했습니다: {error_message}")
        return []
    return events or []
