import requests
import streamlit as st

BACKEND_URL = "http://localhost:8100"

st.title("A360 Assistant Ops")
st.write("최소 튜토리얼 화면입니다.")

if st.button("백엔드 상태 확인"):
    try:
        resp = requests.get(f"{BACKEND_URL}/health", timeout=5)
        st.success(resp.json())
    except requests.RequestException as e:
        st.error(f"백엔드 연결 실패: {e}")

st.divider()
st.subheader("RAG 데이터 적재")
st.caption("버튼을 누르면 백엔드가 크롤링→빌드→pgvector/OpenSearch 적재를 순서대로 실행합니다 (몇 분~몇십 분 소요).")

col1, col2 = st.columns(2)
with col1:
    run_option1 = st.button("옵션 1: JAR 있는 패키지만 적재", use_container_width=True)
with col2:
    run_option2 = st.button("옵션 2: + JAR 없는 패키지 리프도 참고용 적재", use_container_width=True)

if run_option1 or run_option2:
    option = 1 if run_option1 else 2
    try:
        resp = requests.post(f"{BACKEND_URL}/rag/ingest", params={"option": option}, timeout=5)
        if resp.status_code == 200:
            st.success(f"옵션 {option} 시작됨 — 아래 '진행 상태 확인'으로 완료 여부를 확인하세요.")
        else:
            st.warning(resp.json().get("detail", resp.text))
    except requests.RequestException as e:
        st.error(f"백엔드 연결 실패: {e}")

if st.button("진행 상태 확인"):
    try:
        resp = requests.get(f"{BACKEND_URL}/rag/ingest/status", timeout=5)
        status = resp.json()
        if status["running"]:
            st.info(f"옵션 {status['option']} 실행 중...")
        elif status["returncode"] is None:
            st.write("아직 실행한 적 없음.")
        elif status["returncode"] == 0:
            st.success("마지막 실행 성공적으로 완료됨.")
        else:
            st.error(f"마지막 실행 실패 (종료 코드 {status['returncode']}).")
        if status["log"]:
            with st.expander("로그 보기"):
                st.text(status["log"][-5000:])
    except requests.RequestException as e:
        st.error(f"백엔드 연결 실패: {e}")
