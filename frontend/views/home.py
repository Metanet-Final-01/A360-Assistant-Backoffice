import requests
import streamlit as st

from components.layout import page_header
from config import BACKEND_URL


def render() -> None:
    page_header("HOME", "A360 Assistant Ops")
    st.write("최소 튜토리얼 화면입니다.")

    if st.button("백엔드 상태 확인"):
        try:
            resp = requests.get(f"{BACKEND_URL}/health", timeout=5)
            st.success(resp.json())
        except requests.RequestException as e:
            st.error(f"백엔드 연결 실패: {e}")
