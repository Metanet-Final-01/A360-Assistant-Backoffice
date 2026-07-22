import pandas as pd
import streamlit as st

from components.layout import card, section_header

from .api import get_runs, metric_values, post_json
from .live_log import render_polling_log, render_sse_log, render_status_panel

CHUNK_EXPERIMENT_SOURCE = "ragas_chunk_experiment"
CHUNK_SIZE_CHOICES = [300, 600, 900, 1200, 1500]
CHUNK_EXPERIMENT_METRICS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "answer_correctness",
    "hit_at_1",
    "hit_at_3",
    "hit_at_5",
    "reciprocal_rank",
    "evidence_coverage",
)

# 이름에 출처를 붙여둔다 — RAGAS 라이브러리가 LLM으로 판정하는 지표(RAGAS 접두어)와
# 저희가 골드셋의 reference_doc_ids/reference_contexts로 직접 계산하는 지표(Gold 접두어)는
# 신뢰 성격이 달라서(전자는 LLM 판단, 후자는 결정론적 문자열/ID 매칭) 헷갈리면 안 된다.
METRIC_LABELS = {
    "faithfulness": "RAGAS Faithful",
    "answer_relevancy": "RAGAS AnsRel",
    "context_precision": "RAGAS CtxPrec",
    "context_recall": "RAGAS CtxRec",
    "answer_correctness": "RAGAS AnsCorrect",
    "hit_at_1": "Gold Hit@1",
    "hit_at_3": "Gold Hit@3",
    "hit_at_5": "Gold Hit@5",
    "reciprocal_rank": "Gold MRR",
    "evidence_coverage": "Gold EvidCov(exact)",
}


def render_chunk_experiment_tab() -> None:
    render_execution_section()
    st.divider()
    render_results_section()


def render_execution_section() -> None:
    with card("chunk_exp_run"):
        section_header(
            "chunk_size 비교",
            "여러 chunk_size 후보를 같은 케이스로 비교합니다.",
        )
        render_prepare_embeddings_button()
        render_start_form()
        render_status_panel(
            "/eval/ragas/chunk-experiment/execution/status",
            "실험 실행 중...",
            "실험 실패",
            "아직 실행된 chunk_size 실험이 없습니다.",
        )

    render_sse_log("chunk_exp_sse_log", "/eval/ragas/chunk-experiment/execution/events")
    render_chunk_polling_log()


def render_prepare_embeddings_button() -> None:
    if not st.button("질문 임베딩 미리 준비", key="chunk_exp_prepare_embeddings"):
        return

    with st.spinner("승인된 골드셋 질문 임베딩 캐시를 준비하는 중..."):
        success, error_message = post_json(
            "/eval/ragas/chunk-experiment/prepare-embeddings",
            timeout_seconds=120,
        )
    if success:
        st.success("질문 임베딩 준비가 끝났습니다.")
    else:
        st.error(f"임베딩 준비 실패: {error_message}")


def render_start_form() -> None:
    with st.form("chunk_exp_run_form"):
        chunk_sizes = st.multiselect(
            "chunk_size 후보",
            CHUNK_SIZE_CHOICES,
            default=CHUNK_SIZE_CHOICES,
            key="chunk_exp_sizes",
        )
        overlap_column, top_k_column, max_cases_column = st.columns(3)
        overlap = overlap_column.number_input("overlap", min_value=0, value=0, step=50, key="chunk_exp_overlap")
        top_k = top_k_column.number_input("top_k", min_value=1, value=5, step=1, key="chunk_exp_top_k")
        max_cases = max_cases_column.number_input(
            "테스트 케이스 제한(0=전체)",
            min_value=0,
            value=0,
            step=1,
            key="chunk_exp_max_cases",
        )
        start_clicked = st.form_submit_button("실험 시작", type="primary")

    if start_clicked:
        start_chunk_experiment(chunk_sizes, int(overlap), int(top_k), int(max_cases))


def start_chunk_experiment(chunk_sizes: list[int], overlap: int, top_k: int, max_cases: int) -> None:
    if not chunk_sizes:
        st.error("chunk_size를 하나 이상 선택하세요.")
        return

    payload = {
        "chunk_sizes": chunk_sizes,
        "overlap": overlap,
        "top_k": top_k,
    }
    if max_cases:
        payload["max_cases"] = max_cases

    started, error_message = post_json("/eval/ragas/chunk-experiment/execution", payload)
    if started:
        st.success("chunk_size 실험을 시작했습니다. 아래 로그에서 진행 상황을 확인하세요.")
    else:
        st.error(f"실험 시작 실패: {error_message}")


@st.fragment(run_every="2s")
def render_chunk_polling_log() -> None:
    render_polling_log("chunk_exp_polling_log", "/eval/ragas/chunk-experiment/execution/status")


def render_results_section() -> None:
    runs = get_runs(CHUNK_EXPERIMENT_SOURCE)
    with card("chunk_exp_results_summary"):
        section_header("조합별 결과", "chunk_size와 overlap 조합별 평균 지표입니다.")
        if not runs:
            st.info("아직 실험 결과가 없습니다. 먼저 실험을 실행하세요.")
            return

        selected_source_type = select_source_type(runs)
        filtered_runs = filter_runs_by_source_type(runs, selected_source_type)
        st.dataframe(pd.DataFrame(build_summary_rows(filtered_runs)), width="stretch", hide_index=True)

    with card("chunk_exp_results_cases"):
        section_header("케이스별 결과", "질문, 검색 순위, 주요 지표를 확인합니다.")
        selected_label = select_agent_label(filtered_runs)
        case_runs = filter_runs_by_label(filtered_runs, selected_label)
        render_case_table(case_runs, selected_source_type, selected_label)


def select_source_type(runs: list[dict]) -> str:
    source_types = sorted({
        (run.get("raw") or {}).get("source_type")
        for run in runs
        if (run.get("raw") or {}).get("source_type")
    })
    return st.selectbox("문서 유형", ["전체", *source_types], key="chunk_exp_breakdown")


def filter_runs_by_source_type(runs: list[dict], selected_source_type: str) -> list[dict]:
    if selected_source_type == "전체":
        return runs
    return [
        run
        for run in runs
        if (run.get("raw") or {}).get("source_type") == selected_source_type
    ]


def select_agent_label(runs: list[dict]) -> str:
    labels = sorted({run.get("agent_label") for run in runs if run.get("agent_label")})
    return st.selectbox("조합 선택", ["전체", *labels], key="chunk_exp_case_label")


def filter_runs_by_label(runs: list[dict], selected_label: str) -> list[dict]:
    if selected_label == "전체":
        return runs
    return [
        run
        for run in runs
        if run.get("agent_label") == selected_label
    ]


def render_case_table(runs: list[dict], selected_source_type: str, selected_label: str) -> None:
    case_rows = build_case_rows(runs)
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
        key=f"chunk_exp_cases_{selected_source_type}_{selected_label}",
    )
    selected_rows = selection.selection.rows if selection else []
    if selected_rows:
        render_run_detail(runs[selected_rows[0]])


def build_summary_rows(runs: list[dict]) -> list[dict]:
    rows = []
    labels = sorted({run.get("agent_label") for run in runs if run.get("agent_label")})

    for label in labels:
        label_runs = [run for run in runs if run.get("agent_label") == label]
        config = label_runs[0].get("config", {}) if label_runs else {}
        row = {
            "조합": label,
            "chunk_size": config.get("chunk_size"),
            "overlap": config.get("chunk_overlap"),
            "케이스 수": len(label_runs),
        }
        row.update(average_metric_values(label_runs))
        rows.append(row)

    return rows


def average_metric_values(runs: list[dict]) -> dict[str, float | None]:
    averages = {}
    for metric_name in CHUNK_EXPERIMENT_METRICS:
        values = [
            value
            for run in runs
            for name, value in metric_values(run).items()
            if name == metric_name
        ]
        averages[METRIC_LABELS[metric_name]] = round(sum(values) / len(values), 3) if values else None
    return averages


def build_case_rows(runs: list[dict]) -> list[dict]:
    rows = []
    sorted_runs = sorted(runs, key=lambda item: (item.get("agent_label") or "", item["case_id"]))

    for run in sorted_runs:
        raw = run.get("raw") or {}
        metrics = metric_values(run)
        row = {
            "조합": run.get("agent_label") or "-",
            "case_id": run["case_id"],
            "질문 유형": raw.get("question_type") or "-",
            "질문": raw.get("question", "")[:60],
        }
        for metric_name in ("context_recall", "answer_correctness", "hit_at_1", "reciprocal_rank"):
            row[METRIC_LABELS[metric_name]] = round(metrics[metric_name], 3) if metric_name in metrics else None
        rows.append(row)

    return rows


def render_run_detail(run: dict) -> None:
    raw = run.get("raw") or {}
    metrics = metric_values(run)
    st.divider()
    st.subheader(f"상세 - {run['case_id']} ({run.get('agent_label')})")

    left_column, right_column = st.columns(2)
    with left_column:
        st.text("질문")
        st.write(raw.get("question", ""))
        st.text("기준 정답")
        st.write(raw.get("ground_truth", ""))
        st.text("생성 답변")
        st.write(raw.get("answer", ""))

    with right_column:
        st.text("검색 문서 id")
        st.code("\n".join(raw.get("retrieved_parent_ids") or []), language="text")
        st.text("지표")
        st.json({
            metric_name: metrics.get(metric_name)
            for metric_name in CHUNK_EXPERIMENT_METRICS
            if metric_name in metrics
        })
