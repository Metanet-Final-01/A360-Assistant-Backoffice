import requests
import streamlit as st

from config import OPS_BACKEND_URL

HTTP_TIMEOUT_SECONDS = 8
request_session = requests.Session()


def get_json(path: str, params: dict | None = None) -> tuple[object | None, str | None]:
    try:
        response = request_session.get(
            f"{OPS_BACKEND_URL}{path}",
            params=params or {},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json(), None
    except (requests.RequestException, ValueError) as error:
        return None, str(error)


def post_json(path: str, payload: dict | None = None, timeout_seconds: int = HTTP_TIMEOUT_SECONDS) -> tuple[bool, str]:
    try:
        response = request_session.post(
            f"{OPS_BACKEND_URL}{path}",
            json=payload or {},
            timeout=timeout_seconds,
        )
        if response.status_code == 200:
            return True, ""
        return False, read_error_message(response)
    except (requests.RequestException, ValueError) as error:
        return False, str(error)


def read_error_message(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail or response.text)


def get_runs(source: str) -> list[dict]:
    data, error_message = get_json("/eval/runs", {"source": source})
    if error_message:
        st.warning(f"평가 결과를 불러오지 못했습니다: {error_message}")
        return []
    return data or []


def metric_values(run: dict) -> dict[str, float]:
    return {
        metric["name"]: metric["value"]
        for metric in run.get("metrics", [])
    }
