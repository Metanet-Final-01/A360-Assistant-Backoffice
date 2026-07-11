import json

import requests
import streamlit as st

from components.layout import card, page_header, section_header
from config import BACKEND_URL


def render() -> None:
    page_header("EVALUATION", "평가 준비", "데이터셋을 버전별로 등록하고 채점 입력 형식을 확인합니다.")
    datasets = _render_dataset_registry()
    _render_result_registration(datasets)
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
                    response = requests.post(f"{BACKEND_URL}/eval/datasets", json=payload, timeout=5)
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
            response = requests.get(f"{BACKEND_URL}/eval/datasets", timeout=5)
            response.raise_for_status()
            st.session_state["eval_datasets"] = response.json()
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"데이터셋 목록을 불러오지 못했습니다: {exc}")
            st.session_state["eval_datasets"] = []
    return st.session_state["eval_datasets"]


def _render_result_registration(datasets: list[dict]) -> None:
    with card("result_registration"):
        section_header("평가 결과 등록", "실행과 데이터셋 정보를 함께 남기면 이후 결과를 재현하고 비교하기 쉽습니다.")
        if not datasets:
            st.info("먼저 평가 데이터셋을 등록하세요.")
            return

        options = {f"{item['name']} · {item['dataset_id']}@{item['version']}": item for item in datasets}
        with st.form("result_form"):
            selected_name = st.selectbox("데이터셋", list(options))
            selected = options[selected_name]
            case_id = st.selectbox("case_id", selected["case_ids"])
            top = st.columns(3)
            evaluation_id = top[0].text_input("evaluation_id", placeholder="eval-2026-07-11-a")
            agent_label = top[1].text_input("agent_label", placeholder="dev-v2")
            source = top[2].selectbox("채점 방식", ["pm4py", "worfbench", "manual", "rule_check"])
            meta = st.columns(2)
            commit_sha = meta[0].text_input("commit SHA", placeholder="선택")
            config_text = meta[1].text_input("실행 설정 JSON", value="{}")
            passed_value = st.selectbox("통과 여부", ["기록 안 함", "통과", "실패"])
            use_score = st.checkbox("대표 점수를 직접 기록")
            score = st.number_input("대표 점수", min_value=0.0, max_value=1.0, value=0.0, step=0.01, disabled=not use_score)
            raw_text = st.text_area("채점 원본 JSON", value="{}", height=180)
            submitted = st.form_submit_button("결과 저장", type="primary")

        if submitted:
            try:
                raw = json.loads(raw_text)
                config = json.loads(config_text)
                if not isinstance(raw, dict) or not isinstance(config, dict):
                    raise ValueError("원본과 실행 설정은 JSON 객체여야 합니다")
            except (json.JSONDecodeError, ValueError) as exc:
                st.error(f"JSON 형식을 확인하세요: {exc}")
                return
            payload = {
                "evaluation_id": evaluation_id or None,
                "dataset_id": selected["dataset_id"],
                "dataset_version": selected["version"],
                "case_id": case_id,
                "source": source,
                "agent_label": agent_label or None,
                "commit_sha": commit_sha or None,
                "config": config,
                "passed": {"통과": True, "실패": False}.get(passed_value),
                "score": score if use_score else None,
                "raw": raw or None,
            }
            try:
                response = requests.post(f"{BACKEND_URL}/eval/runs", json=payload, timeout=5)
                if response.status_code == 200:
                    st.session_state.pop("eval_runs", None)
                    st.success(f"결과를 저장했습니다: {response.json()['run_id']}")
                else:
                    detail = response.json().get("detail", response.text)
                    st.error(detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))
            except (requests.RequestException, ValueError) as exc:
                st.error(f"결과 저장 실패: {exc}")


def _render_format_guide() -> None:
    with card("format_guide"):
        section_header("채점 입력·출력 형식", "외부 채점 결과를 저장할 때는 아래 원본 형식을 유지합니다.")
        try:
            response = requests.get(f"{BACKEND_URL}/eval/format-guide", timeout=5)
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
