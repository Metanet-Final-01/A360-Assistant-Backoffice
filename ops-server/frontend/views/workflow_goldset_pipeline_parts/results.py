"""가장 최근 실행 결과를 표로 보여주는 부분."""

import pandas as pd
import streamlit as st

from components.layout import card, section_header

from .api import fetch_status

STATUS_PATH = "/eval/workflow-goldset-pipeline/execution/status"


def render_results_section() -> None:
    status = fetch_status(STATUS_PATH)
    results = (status or {}).get("results") or []

    with card("goldset_pipeline_results"):
        section_header("최근 실행 결과", "마지막으로 실행한 파이프라인에서 나온 워크플로우 파일별 변환 결과입니다.")

        if not results:
            st.info("아직 결과가 없습니다. 위에서 zip을 업로드하거나 텍스트를 붙여넣어 실행하세요.")
            return

        table_rows = [_build_result_row(result) for result in results]
        st.dataframe(pd.DataFrame(table_rows), width="stretch", hide_index=True)
        st.caption("output_dir은 ops-server/backend/data 밑 경로입니다 (pm4py .pnml/.ptml, canonical.json, worfbench.json이 저장된 곳).")


def _build_result_row(result: dict) -> dict:
    return {
        "워크플로우 파일": result.get("manifest_path"),
        "canonical 스텝 수": result.get("canonical_step_count"),
        "pm4py leaf 수": result.get("pm4py_leaf_count"),
        "worfbench 충실도": result.get("worfbench_fidelity"),
        "worfbench 액션 수": result.get("worfbench_action_count"),
        "저장 위치": result.get("output_dir"),
    }
