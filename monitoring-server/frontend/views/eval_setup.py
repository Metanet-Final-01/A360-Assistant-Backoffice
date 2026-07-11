import requests
import streamlit as st

from components.layout import card, page_header, section_header
from config import MONITORING_BACKEND_URL


def render() -> None:
    page_header("EVALUATION", "평가 준비", "데이터셋을 버전별로 등록하고 채점 입력 형식을 확인합니다.")
    datasets = _render_dataset_registry()
    _render_evaluation_execution(datasets)
    _render_format_guide()


def _render_dataset_registry() -> list[dict]:
    with card("dataset_registry"):
        section_header("평가 데이터셋", "한 줄에 하나씩 case_id를 입력해 재현 가능한 평가 범위를 고정합니다.")
        datasets = _load_datasets()
        if datasets:
            st.dataframe([{"dataset_id": d["dataset_id"], "이름": d["name"], "버전": d["version"], "케이스 수": len(d["case_ids"]), "설명": d.get("description") or ""} for d in datasets], width="stretch", hide_index=True)
        with st.expander("새 데이터셋 등록", expanded=not datasets):
            with st.form("dataset_form"):
                col1, col2, col3 = st.columns(3)
                dataset_id = col1.text_input("dataset_id", placeholder="workflow-goldset")
                name = col2.text_input("표시 이름", placeholder="워크플로우 골드셋")
                version = col3.text_input("버전", placeholder="2026.07")
                description = st.text_input("설명", placeholder="평가 범위와 변경 이유")
                case_text = st.text_area("case_id 목록", placeholder="web_excel_email_001\ninvoice_processing_001", height=150)
                submitted = st.form_submit_button("데이터셋 등록", type="primary")
            if submitted:
                payload = {"dataset_id": dataset_id, "name": name, "version": version, "description": description or None,
                           "case_ids": [line.strip() for line in case_text.splitlines() if line.strip()]}
                try:
                    response = requests.post(f"{MONITORING_BACKEND_URL}/eval/datasets", json=payload, timeout=5)
                    if response.status_code == 200:
                        st.session_state.pop("eval_datasets", None)
                        st.success("데이터셋을 등록했습니다.")
                        st.rerun()
                    else:
                        st.error(response.json().get("detail", response.text))
                except (requests.RequestException, ValueError) as exc:
                    st.error(f"등록 실패: {exc}")
        return datasets


def _load_datasets() -> list[dict]:
    if "eval_datasets" not in st.session_state:
        try:
            response = requests.get(f"{MONITORING_BACKEND_URL}/eval/datasets", timeout=5)
            response.raise_for_status()
            st.session_state["eval_datasets"] = response.json()
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"데이터셋 목록을 불러오지 못했습니다: {exc}")
            st.session_state["eval_datasets"] = []
    return st.session_state["eval_datasets"]


def _render_evaluation_execution(datasets: list[dict]) -> None:
    with card("evaluation_execution"):
        section_header("평가 실행", "pm4py와 WorFBench를 순서대로 실행하고 선택한 데이터셋 결과를 자동 저장합니다.")
        if not datasets:
            st.info("먼저 평가 데이터셋을 등록하세요.")
            return
        try:
            options_response = requests.get(f"{MONITORING_BACKEND_URL}/eval/execution/options", timeout=5)
            options_response.raise_for_status()
            prediction_labels = options_response.json().get("prediction_labels", [])
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"평가 입력 목록을 불러오지 못했습니다: {exc}")
            return
        if not prediction_labels:
            st.info("a360-eval-sandbox/Metadata에 predictions_from_agent_<label>.json 파일이 없습니다.")
            return

        dataset_options = {f"{item['name']} · {item['dataset_id']}@{item['version']}": item for item in datasets}
        with st.form("execution_form"):
            selected_dataset_name = st.selectbox("평가 데이터셋", list(dataset_options), key="execute_dataset")
            prediction_label = st.selectbox("예측 입력", prediction_labels, format_func=lambda value: f"predictions_from_agent_{value}.json")
            cols = st.columns(3)
            evaluation_id = cols[0].text_input("evaluation_id", placeholder="eval-2026-07-11-v2", key="execute_id")
            agent_label = cols[1].text_input("결과 버전", value=prediction_label, key="execute_agent")
            commit_sha = cols[2].text_input("commit SHA", placeholder="선택", key="execute_commit")
            start = st.form_submit_button("평가 시작", type="primary", use_container_width=True)

        if start:
            dataset = dataset_options[selected_dataset_name]
            payload = {
                "prediction_label": prediction_label,
                "evaluation_id": evaluation_id,
                "dataset_id": dataset["dataset_id"],
                "dataset_version": dataset["version"],
                "agent_label": agent_label,
                "commit_sha": commit_sha or None,
            }
            try:
                response = requests.post(f"{MONITORING_BACKEND_URL}/eval/execution", json=payload, timeout=5)
                if response.status_code == 200:
                    st.success("평가를 시작했습니다. 아래 상태 새로고침으로 진행 상황을 확인하세요.")
                else:
                    st.error(response.json().get("detail", response.text))
            except (requests.RequestException, ValueError) as exc:
                st.error(f"평가 시작 실패: {exc}")

        if st.button("평가 상태 새로고침", key="execution_refresh"):
            st.session_state.pop("eval_execution_status", None)
        try:
            status_response = requests.get(f"{MONITORING_BACKEND_URL}/eval/execution/status", timeout=5)
            status_response.raise_for_status()
            status = status_response.json()
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"평가 상태를 불러오지 못했습니다: {exc}")
            return

        if status.get("running"):
            stage_labels = {"pm4py": "pm4py 채점", "worfbench": "WorFBench 채점", "saving": "결과 저장"}
            st.info(f"실행 중 · {stage_labels.get(status.get('stage'), status.get('stage'))}")
        elif status.get("returncode") == 0:
            st.success(f"평가 완료 · 결과 {status.get('saved', 0)}건 저장")
        elif status.get("returncode"):
            st.error(f"평가 실패: {status.get('error')}")
        else:
            st.caption("아직 실행한 평가가 없습니다.")
        if status.get("log"):
            with st.expander("평가 로그"):
                st.code(status["log"][-8000:], language="text")


def _render_format_guide() -> None:
    with card("format_guide"):
        section_header("채점 입력·출력 형식", "외부 채점 결과를 저장할 때는 아래 원본 형식을 유지합니다.")
        try:
            response = requests.get(f"{MONITORING_BACKEND_URL}/eval/format-guide", timeout=5)
            response.raise_for_status()
            guide = response.json()
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"포맷 안내를 불러오지 못했습니다: {exc}")
            return
        tabs = st.tabs(["pm4py", "WorFBench"])
        for tab, engine in zip(tabs, ("pm4py", "worfbench")):
            with tab:
                section = guide[engine]
                st.write(section["summary"])
                left, right = st.columns(2)
                with left:
                    st.caption(section["input_example"]["note"])
                    st.json(section["input_example"]["value"], expanded=False)
                with right:
                    st.caption(section["output_example"]["note"])
                    st.json(section["output_example"]["value"], expanded=False)
