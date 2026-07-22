import pandas as pd
import streamlit as st

from components.layout import card, section_header

from .api import get_runs, metric_values, post_json
from .live_log import render_polling_log, render_sse_log, render_status_panel

RAGAS_SOURCE = "ragas"
RAGAS_METRICS = (
    "ragas_faithfulness",
    "ragas_answer_relevancy",
    "ragas_context_precision",
    "ragas_context_recall",
)


def fetch_ragas_runs() -> list[dict]:
    return get_runs(RAGAS_SOURCE)


def render_execution_tab(approved_case_count: int) -> None:
    with card("ragas_eval_run"):
        section_header(
            "평가 실행",
            "승인된 RAGAS 케이스로 RAG 검색 품질을 평가합니다.",
        )
        st.caption(f"이번 실행 대상: 승인 케이스 {approved_case_count}건")

        with st.form("ragas_eval_run_form"):
            agent_label = st.text_input("결과 버전", value="ragas-100", key="ragas_eval_agent_label")
            judge_model = st.text_input("Judge 모델", value="gpt-4o-mini", key="ragas_eval_judge_model")
            start_clicked = st.form_submit_button("RAGAS 평가 시작", type="primary")

        if start_clicked:
            start_ragas_evaluation(agent_label, judge_model)

        render_status_panel(
            "/eval/ragas/execution/status",
            "평가 실행 중...",
            "평가 실패",
            "아직 실행된 RAGAS 평가가 없습니다.",
        )

    render_sse_log("ragas_eval_sse_log", "/eval/ragas/execution/events")
    render_ragas_polling_log()


def start_ragas_evaluation(agent_label: str, judge_model: str) -> None:
    payload = {
        "agent_label": agent_label.strip() or "ragas-100",
        "judge_model": judge_model.strip() or "gpt-4o-mini",
    }
    started, error_message = post_json("/eval/ragas/execution", payload)
    if started:
        st.success("RAGAS 평가를 시작했습니다. 아래 로그에서 진행 상황을 확인하세요.")
    else:
        st.error(f"평가 시작 실패: {error_message}")


@st.fragment(run_every="2s")
def render_ragas_polling_log() -> None:
    render_polling_log("ragas_eval_polling_log", "/eval/ragas/execution/status")


def render_results_tab(ragas_runs: list[dict]) -> None:
    with card("ragas_eval_results_summary"):
        section_header("버전별 결과", "RAGAS 지표 평균입니다. 모든 지표는 높을수록 좋습니다.")
        if not ragas_runs:
            st.info("아직 RAGAS 결과가 없습니다. 먼저 평가를 실행하세요.")
            return

        selected_label = select_result_label(ragas_runs)
        filtered_runs = filter_runs_by_label(ragas_runs, selected_label)
        st.dataframe(pd.DataFrame(build_summary_rows(filtered_runs)), width="stretch", hide_index=True)

    with card("ragas_eval_results_cases"):
        section_header("케이스별 결과", "질문, 오류, 지표를 케이스 단위로 확인합니다.")
        case_rows = build_case_rows(filtered_runs)
        if not case_rows:
            st.info("표시할 케이스 결과가 없습니다.")
            return

        selection = st.dataframe(
            pd.DataFrame(case_rows),
            width="stretch",
            hide_index=True,
            height=420,
            on_select="rerun",
            selection_mode="single-row",
            key=f"ragas_eval_cases_{selected_label}",
        )
        selected_rows = selection.selection.rows if selection else []
        if selected_rows:
            render_run_detail(filtered_runs[selected_rows[0]])


def select_result_label(ragas_runs: list[dict]) -> str:
    labels = sorted({run.get("agent_label") for run in ragas_runs if run.get("agent_label")})
    return st.selectbox("결과 버전", ["전체", *labels], key="ragas_eval_result_label")


def filter_runs_by_label(ragas_runs: list[dict], selected_label: str) -> list[dict]:
    if selected_label == "전체":
        return ragas_runs
    return [
        run
        for run in ragas_runs
        if run.get("agent_label") == selected_label
    ]


def build_summary_rows(ragas_runs: list[dict]) -> list[dict]:
    rows = []
    labels = sorted({run.get("agent_label") for run in ragas_runs if run.get("agent_label")})

    for label in labels:
        label_runs = [run for run in ragas_runs if run.get("agent_label") == label]
        failed_count = count_failed_runs(label_runs)
        row = {
            "버전": label,
            "케이스 수": len(label_runs),
            "성공": len(label_runs) - failed_count,
            "실패": failed_count,
        }
        row.update(average_metric_values(label_runs, RAGAS_METRICS))
        rows.append(row)

    return rows


def count_failed_runs(runs: list[dict]) -> int:
    return sum(1 for run in runs if (run.get("raw") or {}).get("error"))


def average_metric_values(runs: list[dict], metric_names: tuple[str, ...]) -> dict[str, float | None]:
    averages = {}
    for metric_name in metric_names:
        values = [
            metric["value"]
            for run in runs
            for metric in run.get("metrics", [])
            if metric["name"] == metric_name
        ]
        averages[metric_name] = round(sum(values) / len(values), 3) if values else None
    return averages


def build_case_rows(ragas_runs: list[dict]) -> list[dict]:
    rows = []
    sorted_runs = sorted(ragas_runs, key=lambda item: (item.get("agent_label") or "", item["case_id"]))

    for run in sorted_runs:
        raw = run.get("raw") or {}
        metrics = metric_values(run)
        row = {
            "버전": run.get("agent_label") or "-",
            "case_id": run["case_id"],
            "질문": raw.get("question", ""),
            "오류": raw.get("error") or "",
        }
        for metric_name in RAGAS_METRICS:
            row[metric_name] = round(metrics[metric_name], 3) if metric_name in metrics else None
        rows.append(row)

    return rows


def render_run_detail(run: dict) -> None:
    raw = run.get("raw") or {}
    st.divider()
    st.subheader(f"상세 - {run['case_id']}")

    left_column, right_column = st.columns(2)
    with left_column:
        st.text("질문")
        st.write(raw.get("question", ""))
        st.text("기준 정답")
        st.write(raw.get("ground_truth", ""))
        st.text("생성 답변")
        st.write(raw.get("response", ""))

    with right_column:
        st.text("검색 문서 ID")
        st.code("\n".join(raw.get("retrieved_doc_ids") or []), language="text")
        if raw.get("error"):
            st.error(raw["error"])

    with st.expander("검색 컨텍스트"):
        for index, context in enumerate(raw.get("retrieved_contexts") or [], start=1):
            st.caption(f"문서 {index}")
            st.code(context, language=None)
