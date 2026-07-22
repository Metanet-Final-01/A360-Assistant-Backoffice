"""백엔드 호출용 작은 도우미 함수들. 이 페이지 전용이라 다른 페이지(ragas_evaluation
등)의 api.py와는 따로 둔다 — 서로 다른 화면끼리 얽히지 않게 하기 위해서다."""

import requests
import streamlit as st

from config import OPS_BACKEND_URL

HTTP_TIMEOUT_SECONDS = 8
UPLOAD_TIMEOUT_SECONDS = 30

request_session = requests.Session()


def get_json(path: str) -> tuple[object | None, str | None]:
    try:
        response = request_session.get(f"{OPS_BACKEND_URL}{path}", timeout=HTTP_TIMEOUT_SECONDS)
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


def post_file_upload(path: str, file_name: str, file_bytes: bytes) -> tuple[bool, str]:
    try:
        response = request_session.post(
            f"{OPS_BACKEND_URL}{path}",
            files={"file": (file_name, file_bytes, "application/zip")},
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
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


def fetch_status(status_path: str) -> dict | None:
    status, error_message = get_json(status_path)
    if error_message:
        st.warning(f"상태를 불러오지 못했습니다: {error_message}")
        return None
    return status
