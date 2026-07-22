"""pm4py/WorFBench 결과 조회 및 버전 비교."""

import pandas as pd
import requests
import streamlit as st

from components.layout import card, metric_strip, section_header

from .api import fetch_runs, request_session
from config import OPS_BACKEND_URL

WORKFLOW_SOURCES = ("pm4py", "worfbench")
FIXED_METRICS = ("pm4py_fitness", "pm4py_precision", "worfbench_precision", "worfbench_recall", "worfbench_f1_score")


def render_results_tab() -> None:
    runs = [run for run in fetch_runs() if run.get("source") in WORKFLOW_SOURCES]
    metric_strip([
        ("Workflow 결과", len(runs)),
        ("결과 버전", len({run.get("agent_label") for run in runs if run.get("agent_label")})),
    ])
    render_run_table(runs)
    render_version_comparison(runs)


def _metrics_of(run: dict) -> dict[str, float]:
    return {item["name"]: item["value"] for item in run.get("metrics", [])}


def _label_of(run: dict) -> str:
    return f"{run['case_id']} · {run['source']} · {run.get('agent_label') or '-'} · {(run.get('run_id') or '-')[:8]}"


def render_run_table(runs: list[dict]) -> None:
    with card("workflow_run_table"):
        section_header("결과 로그", "케이스별 원본과 공통 지표를 확인합니다.")
        if not runs:
            st.info("저장된 Workflow 평가 결과가 없습니다. 위 탭에서 먼저 평가를 실행하세요.")
            return

        source_column, agent_column, dataset_column = st.columns(3)
        source_filter = source_column.selectbox("채점 방식", ["전체", *WORKFLOW_SOURCES])
        agent_filter = agent_column.selectbox("버전", ["전체", *sorted({run["agent_label"] for run in runs if run.get("agent_label")})])
        dataset_filter = dataset_column.selectbox("데이터셋", ["전체", *sorted({run["dataset_id"] for run in runs if run.get("dataset_id")})])

        filtered_runs = runs
        if source_filter != "전체":
            filtered_runs = [run for run in filtered_runs if run["source"] == source_filter]
        if agent_filter != "전체":
            filtered_runs = [run for run in filtered_runs if run.get("agent_label") == agent_filter]
        if dataset_filter != "전체":
            filtered_runs = [run for run in filtered_runs if run.get("dataset_id") == dataset_filter]

        table_rows = [{
            "case_id": run["case_id"], "채점 방식": run["source"], "버전": run.get("agent_label") or "-",
            "데이터셋": f"{run.get('dataset_id') or '-'}@{run.get('dataset_version') or '-'}",
            "score": run.get("score"), "passed": run.get("passed"),
            "기록 시각": run["logged_at"][:19].replace("T", " "),
        } for run in filtered_runs]

        selection = st.dataframe(
            pd.DataFrame(table_rows), width="stretch", hide_index=True,
            on_select="rerun", selection_mode="multi-row",
            key=f"workflow_run_table_{source_filter}_{agent_filter}_{dataset_filter}",
        )
        selected_runs = [filtered_runs[index] for index in selection.selection.rows if index < len(filtered_runs)]
        if len(selected_runs) == 2:
            render_two_run_comparison(selected_runs)
        elif len(selected_runs) > 2:
            st.info("두 결과만 선택하면 공통 지표를 비교합니다.")


def render_two_run_comparison(selected_runs: list[dict]) -> None:
    section_header("선택 결과 비교")
    run_a, run_b = selected_runs
    metrics_a, metrics_b = _metrics_of(run_a), _metrics_of(run_b)
    shared_metric_names = sorted(set(metrics_a) & set(metrics_b))
    if shared_metric_names:
        render_delta_table(shared_metric_names, metrics_a, metrics_b, _label_of(run_a), _label_of(run_b))
    else:
        st.info("두 결과에 공통 지표가 없습니다.")

    left_column, right_column = st.columns(2)
    with left_column.expander(f"A · {_label_of(run_a)} 원본"):
        st.json(run_a.get("raw") or {})
    with right_column.expander(f"B · {_label_of(run_b)} 원본"):
        st.json(run_b.get("raw") or {})


def _paired_averages(runs: list[dict], label_a: str, label_b: str) -> tuple[dict, dict, dict]:
    def grouped_by_case(label: str) -> dict[str, dict[str, float]]:
        buckets: dict[str, dict[str, list[float]]] = {}
        for run in runs:
            if run.get("agent_label") != label:
                continue
            for metric_name, metric_value in _metrics_of(run).items():
                if metric_name in FIXED_METRICS:
                    buckets.setdefault(run["case_id"], {}).setdefault(metric_name, []).append(metric_value)
        return {
            case_id: {name: sum(values) / len(values) for name, values in case_metrics.items()}
            for case_id, case_metrics in buckets.items()
        }

    group_a, group_b = grouped_by_case(label_a), grouped_by_case(label_b)
    paired_values: dict[str, list[tuple[str, float, float]]] = {}
    for case_id in sorted(set(group_a) & set(group_b)):
        for metric_name in FIXED_METRICS:
            if metric_name in group_a[case_id] and metric_name in group_b[case_id]:
                paired_values.setdefault(metric_name, []).append((case_id, group_a[case_id][metric_name], group_b[case_id][metric_name]))

    average_a = {name: sum(a for _, a, _ in values) / len(values) for name, values in paired_values.items()}
    average_b = {name: sum(b for _, _, b in values) / len(values) for name, values in paired_values.items()}
    return average_a, average_b, paired_values


def render_version_comparison(runs: list[dict]) -> None:
    with card("workflow_version_comparison"):
        section_header("버전 비교", "지표별로 A와 B가 모두 존재하는 동일 case_id만 계산합니다.")
        labels = sorted({run["agent_label"] for run in runs if run.get("agent_label")})
        if len(labels) < 2:
            st.info("서로 다른 agent_label 결과가 두 개 이상 필요합니다.")
            return

        left_column, right_column = st.columns(2)
        label_a = left_column.selectbox("버전 A", labels, index=0)
        label_b = right_column.selectbox("버전 B", labels, index=min(1, len(labels) - 1))
        if label_a == label_b:
            st.warning("서로 다른 버전을 선택하세요.")
            return

        average_a, average_b, paired_values = _paired_averages(runs, label_a, label_b)
        shared_metric_names = [name for name in FIXED_METRICS if name in paired_values]
        if not shared_metric_names:
            st.info("동일 케이스에서 짝지을 수 있는 공통 지표가 없습니다.")
            return

        st.caption(" · ".join(f"{name}: {len(paired_values[name])}쌍" for name in shared_metric_names))
        render_delta_table(shared_metric_names, average_a, average_b, f"A ({label_a})", f"B ({label_b})")
        render_export_button(label_a, label_b)


def render_delta_table(metric_names: list[str], values_a: dict, values_b: dict, label_a: str, label_b: str) -> None:
    rows = []
    for name in metric_names:
        delta = values_b[name] - values_a[name]
        change_rate = f"{delta / values_a[name] * 100:+.1f}%" if values_a[name] else "n/a"
        rows.append({"지표": name, label_a: round(values_a[name], 4), label_b: round(values_b[name], 4), "Δ B-A": round(delta, 4), "변화율": change_rate})

    def _color_delta(value: object) -> str:
        if not isinstance(value, float):
            return ""
        if value > 0:
            return "color:#17845b;font-weight:700"
        if value < 0:
            return "color:#c33d32;font-weight:700"
        return ""

    frame = pd.DataFrame(rows)
    st.dataframe(frame.style.map(_color_delta, subset=["Δ B-A"]), width="stretch", hide_index=True)


def render_export_button(label_a: str, label_b: str) -> None:
    if st.button("Excel 보고서 생성"):
        try:
            response = request_session.get(
                f"{OPS_BACKEND_URL}/eval/export/comparison-xlsx", params={"label_a": label_a, "label_b": label_b}, timeout=15,
            )
            response.raise_for_status()
            st.session_state["workflow_xlsx_export"] = (label_a, label_b, response.content)
        except requests.RequestException as error:
            st.error(f"Excel 생성 실패: {error}")

    cached_export = st.session_state.get("workflow_xlsx_export")
    if cached_export and cached_export[:2] == (label_a, label_b):
        st.download_button(
            "Excel 내려받기", cached_export[2], file_name=f"comparison_{label_a}_vs_{label_b}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
