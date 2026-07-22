"""로그인 게이트 — Backend(A360-Assistant-Backend) 관리자 계정으로만 Ops 화면 접근 허용.

세션 기반(JWT를 프론트에 보관하지 않음): 로그인 성공 시 st.session_state에 로그인 여부만
남기고, Backend가 내려준 토큰 자체는 검증에만 쓰고 버린다. Streamlit은 브라우저 세션(웹소켓
연결)마다 서버 쪽에 상태를 들고 있는 구조라 세션 기반이 이 앱 모델에 자연스럽다.

/api/auth/me는 is_admin을 안 내려줘서(UserOut에 없음), 로그인 직후 관리자 전용 엔드포인트
(/api/admin/budget-limits)를 한번 호출해 200이면 관리자로 인정한다.
"""

import httpx
import streamlit as st

from config import A360_BACKEND_URL

_SESSION_KEY = "ops_authenticated"


def is_authenticated() -> bool:
    return bool(st.session_state.get(_SESSION_KEY))


def _verify_admin(email: str, password: str) -> str | None:
    """이메일/비밀번호를 Backend에 검증하고, 관리자가 아니면 사유 문자열을 돌려준다(성공 시 None)."""
    try:
        login_resp = httpx.post(
            f"{A360_BACKEND_URL}/api/auth/login",
            json={"email": email, "password": password},
            timeout=10.0,
        )
    except httpx.RequestError as e:
        return f"Backend 연결 실패: {e}"

    if login_resp.status_code != 200:
        return "이메일 또는 비밀번호가 올바르지 않습니다."

    access_token = login_resp.json()["access_token"]
    try:
        admin_check = httpx.get(
            f"{A360_BACKEND_URL}/api/admin/budget-limits",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
    except httpx.RequestError as e:
        return f"Backend 연결 실패: {e}"

    if admin_check.status_code == 403:
        return "관리자 계정만 로그인할 수 있습니다."
    if admin_check.status_code != 200:
        return f"관리자 확인 실패({admin_check.status_code})"
    return None


def render_login_screen() -> None:
    st.title("A360 Ops 로그인")
    st.caption("A360-Assistant-Backend 관리자 계정으로 로그인하세요.")
    with st.form("ops_login_form"):
        email = st.text_input("이메일")
        password = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("로그인", use_container_width=True)

    if submitted:
        if not email or not password:
            st.error("이메일과 비밀번호를 입력하세요.")
            return
        error = _verify_admin(email, password)
        if error:
            st.error(error)
            return
        st.session_state[_SESSION_KEY] = True
        st.session_state["ops_user_email"] = email
        st.rerun()


def logout() -> None:
    st.session_state.pop(_SESSION_KEY, None)
    st.session_state.pop("ops_user_email", None)
    st.rerun()
