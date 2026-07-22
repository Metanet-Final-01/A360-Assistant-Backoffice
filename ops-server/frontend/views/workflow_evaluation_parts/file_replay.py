"""사람이 미리 만들어둔 예측 파일(JSON)을 지정해서 pm4py/WorFBench로 다시 채점하는 탭.

라이브 실행과 달리 Backend Agent를 새로 호출하지 않고, a360-eval-sandbox/Metadata의
predictions_from_agent_<label>.json 파일을 그대로 채점 입력으로 쓴다 — 과거 예측
결과를 다른 채점 로직으로 다시 돌려보고 싶을 때 쓴다."""

import streamlit as st

from components.layout import card, section_header

from .api import get_json, post_json

STATUS_PATH = "/eval/execution/status"


def render_file_replay_tab() -> None:
    _render_dataset_registry()
    _render_execution_form()
    _render_format_guide()


def _load_datasets() -> list[dict]:
    if "workflow_eval_datasets" not in st.session_state:
        data, error_message = get_json("/eval/datasets")
        if error_message:
            st.warning(f"데이터셋 목록을 불러오지 못했습니다: {error_message}")
            data = []
        st.session_state["workflow_eval_datasets"] = data
    return st.session_state["workflow_eval_datasets"]


def _render_dataset_registry() -> None:
    with card("workflow_dataset_registry"):
        section_header("평가 범위(데이터셋)", "'Workflow 정답셋' 페이지의 '평가 세트' 탭에서 등록/관리합니다. 여기서는 조회만 합니다.")
        datasets = _load_datasets()
        if datasets:
            rows = [
                {"dataset_id": d["dataset_id"], "이름": d["name"], "버전": d["version"],
                 "케이스 수": len(d["case_ids"]), "설명": d.get("description") or ""}
                for d in datasets
            ]
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.info("등록된 평가 세트가 없습니다. 'Workflow 정답셋' 페이지에서 먼저 등록하세요.")


def _load_prediction_labels() -> list[str] | None:
    if "workflow_prediction_labels" not in st.session_state:
        data, error_message = get_json("/eval/execution/options")
        if error_message:
            st.warning(f"평가 입력 목록을 불러오지 못했습니다: {error_message}")
            return None
        st.session_state["workflow_prediction_labels"] = (data or {}).get("prediction_labels", [])
    return st.session_state["workflow_prediction_labels"]


def _render_execution_form() -> None:
    with card("workflow_file_replay_execution"):
        section_header("예측 파일 재채점", "pm4py와 WorFBench를 순서대로 실행하고 선택한 데이터셋 결과를 자동 저장합니다.")

        datasets = _load_datasets()
        if not datasets:
            st.info("먼저 'Workflow 정답셋' 페이지의 '평가 세트' 탭에서 평가 범위(데이터셋)를 등록하세요.")
            return

        prediction_labels = _load_prediction_labels()
        if prediction_labels is None:
            return
        if not prediction_labels:
            st.info("a360-eval-sandbox/Metadata에 predictions_from_agent_<label>.json 파일이 없습니다.")
            return

        dataset_options = {f"{item['name']} · {item['dataset_id']}@{item['version']}": item for item in datasets}
        with st.form("workflow_file_replay_form"):
            selected_dataset_name = st.selectbox("평가 데이터셋", list(dataset_options), key="workflow_replay_dataset")
            prediction_label = st.selectbox(
                "예측 입력", prediction_labels, format_func=lambda value: f"predictions_from_agent_{value}.json",
            )
            id_column, agent_column, commit_column = st.columns(3)
            evaluation_id = id_column.text_input("evaluation_id", placeholder="eval-2026-07-11-v2", key="workflow_replay_id")
            agent_label = agent_column.text_input("결과 버전", value=prediction_label, key="workflow_replay_agent")
            commit_sha = commit_column.text_input("commit SHA", placeholder="선택", key="workflow_replay_commit")
            start_clicked = st.form_submit_button("평가 시작", type="primary", width="stretch")

        if start_clicked:
            dataset = dataset_options[selected_dataset_name]
            _start_file_replay(prediction_label, evaluation_id, dataset, agent_label, commit_sha)

        _render_execution_status()


def _start_file_replay(prediction_label: str, evaluation_id: str, dataset: dict, agent_label: str, commit_sha: str) -> None:
    payload = {
        "prediction_label": prediction_label,
        "evaluation_id": evaluation_id,
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["version"],
        "agent_label": agent_label,
        "commit_sha": commit_sha or None,
    }
    started, error_message = post_json("/eval/execution", payload)
    if started:
        st.success("평가를 시작했습니다. 아래 상태 새로고침으로 진행 상황을 확인하세요.")
    else:
        st.error(f"평가 시작 실패: {error_message}")


def _render_execution_status() -> None:
    if st.button("평가 상태 새로고침", key="workflow_replay_status_refresh"):
        st.session_state.pop("workflow_execution_status_cache", None)

    status, error_message = get_json(STATUS_PATH)
    if error_message:
        st.warning(f"평가 상태를 불러오지 못했습니다: {error_message}")
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


def _load_format_guide() -> dict | None:
    if "workflow_format_guide" not in st.session_state:
        data, error_message = get_json("/eval/format-guide")
        if error_message:
            st.warning(f"포맷 안내를 불러오지 못했습니다: {error_message}")
            return None
        st.session_state["workflow_format_guide"] = data
    return st.session_state["workflow_format_guide"]


def _render_format_guide() -> None:
    with card("workflow_format_guide"):
        section_header("채점 입력·출력 형식", "예측 파일을 직접 만들 때는 아래 원본 형식을 유지합니다.")
        guide = _load_format_guide()
        if guide is None:
            return
        pm4py_tab, worfbench_tab = st.tabs(["pm4py", "WorFBench"])
        for tab, engine in ((pm4py_tab, "pm4py"), (worfbench_tab, "worfbench")):
            with tab:
                section = guide[engine]
                st.write(section["summary"])
                left_column, right_column = st.columns(2)
                with left_column:
                    st.caption(section["input_example"]["note"])
                    st.json(section["input_example"]["value"], expanded=False)
                with right_column:
                    st.caption(section["output_example"]["note"])
                    st.json(section["output_example"]["value"], expanded=False)
