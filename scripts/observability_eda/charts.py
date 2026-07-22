"""관측 데이터를 여러 종류의 그래프로 그려서 파일로 저장한다.

그래프는 크게 5가지로 나눈다: LLM 사용량, RAG 파이프라인 지연시간, Agent 턴 진행,
API 요청 지표, 평가 결과. 각 함수는 그래프 하나를 그려서 output_dir 밑에 PNG로
저장하고, 저장한 파일 경로를 돌려준다.
"""

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

matplotlib.use("Agg")  # 화면 없이 파일로만 저장 (서버/스크립트 환경)

plt.rcParams["font.family"] = ["Malgun Gothic", "sans-serif"]  # 한글 라벨 깨짐 방지(Windows)
plt.rcParams["axes.unicode_minus"] = False


def _save(figure: plt.Figure, output_dir, file_name: str) -> str:
    output_path = output_dir / file_name
    figure.tight_layout()
    figure.savefig(output_path, dpi=110)
    plt.close(figure)
    return str(output_path)


# ── 1. LLM 사용량 (llm_usage) ──────────────────────────────────────────


def chart_llm_cost_by_component(llm_usage: pd.DataFrame, output_dir) -> str:
    cost_by_component = llm_usage.groupby("component")["cost_usd"].sum().sort_values(ascending=False)
    figure, axis = plt.subplots(figsize=(8, 5))
    cost_by_component.plot(kind="bar", ax=axis, color="#1f6f8b")
    axis.set_title("구성요소(component)별 누적 비용(USD)")
    axis.set_ylabel("비용(USD)")
    return _save(figure, output_dir, "01_llm_cost_by_component.png")


def chart_llm_request_count_by_component(llm_usage: pd.DataFrame, output_dir) -> str:
    count_by_component = llm_usage["component"].value_counts()
    figure, axis = plt.subplots(figsize=(8, 5))
    count_by_component.plot(kind="bar", ax=axis, color="#2f855a")
    axis.set_title("구성요소(component)별 LLM 호출 건수")
    axis.set_ylabel("건수")
    return _save(figure, output_dir, "02_llm_request_count_by_component.png")


def chart_llm_latency_by_component(llm_usage: pd.DataFrame, output_dir) -> str:
    plottable = llm_usage.dropna(subset=["latency_ms"])
    components_with_latency = plottable["component"].unique()
    latency_groups = [plottable.loc[plottable["component"] == component, "latency_ms"] for component in components_with_latency]

    figure, axis = plt.subplots(figsize=(8, 5))
    axis.boxplot(latency_groups, tick_labels=components_with_latency, showfliers=True)
    axis.set_title("구성요소별 지연시간(ms) 분포")
    axis.set_ylabel("지연시간(ms)")
    axis.set_yscale("log")
    return _save(figure, output_dir, "03_llm_latency_by_component_boxplot.png")


def chart_llm_cost_over_time(llm_usage: pd.DataFrame, output_dir) -> str:
    daily_cost = llm_usage.set_index("created_at").resample("1D")["cost_usd"].sum()
    figure, axis = plt.subplots(figsize=(9, 5))
    daily_cost.plot(ax=axis, marker="o", color="#c05621")
    axis.set_title("일별 LLM 비용 추이")
    axis.set_ylabel("비용(USD)")
    axis.set_xlabel("날짜")
    return _save(figure, output_dir, "04_llm_cost_over_time.png")


def chart_llm_tokens_by_component(llm_usage: pd.DataFrame, output_dir) -> str:
    token_totals = llm_usage.groupby("component")[["input_tokens", "output_tokens"]].sum()
    figure, axis = plt.subplots(figsize=(8, 5))
    token_totals.plot(kind="bar", stacked=True, ax=axis, color=["#4299e1", "#f6ad55"])
    axis.set_title("구성요소별 입력/출력 토큰 총량")
    axis.set_ylabel("토큰 수")
    return _save(figure, output_dir, "05_llm_tokens_by_component.png")


# ── 2. RAG 파이프라인 지연시간 (rag_events) ─────────────────────────────


def chart_rag_event_duration_boxplot(rag_events: pd.DataFrame, output_dir) -> str:
    top_events = rag_events["event"].value_counts().head(8).index
    plottable = rag_events[rag_events["event"].isin(top_events) & rag_events["duration_ms"].notna()]
    groups = [plottable.loc[plottable["event"] == event, "duration_ms"] for event in top_events]

    figure, axis = plt.subplots(figsize=(10, 6))
    axis.boxplot(groups, tick_labels=top_events, showfliers=True)
    axis.set_title("RAG 파이프라인 단계별 소요시간(ms) 분포 — 건수 상위 8개")
    axis.set_ylabel("소요시간(ms)")
    axis.set_yscale("log")
    plt.setp(axis.get_xticklabels(), rotation=30, ha="right")
    return _save(figure, output_dir, "06_rag_event_duration_boxplot.png")


def chart_rag_event_p95_bar(rag_events: pd.DataFrame, output_dir) -> str:
    summary = rag_events.groupby("event")["duration_ms"].agg(
        평균="mean", p95=lambda values: values.quantile(0.95), 최대="max",
    ).sort_values("p95", ascending=False)

    figure, axis = plt.subplots(figsize=(9, 6))
    summary[["평균", "p95", "최대"]].plot(kind="bar", ax=axis)
    axis.set_title("RAG 이벤트별 평균/p95/최대 소요시간(ms)")
    axis.set_ylabel("소요시간(ms, log)")
    axis.set_yscale("log")
    plt.setp(axis.get_xticklabels(), rotation=30, ha="right")
    return _save(figure, output_dir, "07_rag_event_p95_max_bar.png")


def chart_rag_hybrid_search_over_time(rag_events: pd.DataFrame, output_dir) -> str:
    hybrid = rag_events[rag_events["event"] == "hybrid_search"].sort_values("created_at")
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.scatter(hybrid["created_at"], hybrid["duration_ms"], s=6, alpha=0.4, color="#e53e3e")
    daily_median = hybrid.set_index("created_at").resample("1D")["duration_ms"].median()
    axis.plot(daily_median.index, daily_median.values, color="#1a202c", linewidth=2, label="일별 중앙값")
    axis.set_title("hybrid_search 소요시간 추이 — 이 시간대에 실제로 느려졌는지 확인용")
    axis.set_ylabel("소요시간(ms)")
    axis.legend()
    return _save(figure, output_dir, "08_rag_hybrid_search_over_time.png")


def chart_rag_error_rate_by_event(rag_events: pd.DataFrame, output_dir) -> str:
    error_rate = rag_events.groupby("event")["status"].apply(lambda values: (values == "error").mean() * 100)
    error_rate = error_rate[error_rate > 0].sort_values(ascending=False)
    if error_rate.empty:
        return ""

    figure, axis = plt.subplots(figsize=(8, 5))
    error_rate.plot(kind="bar", ax=axis, color="#e53e3e")
    axis.set_title("RAG 이벤트별 에러율(%)")
    axis.set_ylabel("에러율(%)")
    return _save(figure, output_dir, "09_rag_error_rate_by_event.png")


def chart_rag_volume_over_time(rag_events: pd.DataFrame, output_dir) -> str:
    daily_volume = rag_events.set_index("created_at").resample("1D").size()
    figure, axis = plt.subplots(figsize=(9, 5))
    daily_volume.plot(kind="bar", ax=axis, color="#3182ce")
    axis.set_title("일별 RAG 이벤트 총량")
    axis.set_ylabel("건수")
    return _save(figure, output_dir, "10_rag_volume_over_time.png")


# ── 3. Agent 턴 진행 (turn_events) ──────────────────────────────────────

_STAGE_ORDER = ["routing", "analyzing", "searching", "recommending", "composing", "verifying", "refining", "reading"]


def chart_turn_stage_funnel(turn_events: pd.DataFrame, output_dir) -> str:
    stage_counts = turn_events[turn_events["kind"] == "stage"]["stage"].value_counts()
    ordered_stages = [stage for stage in _STAGE_ORDER if stage in stage_counts.index]
    ordered_counts = stage_counts.reindex(ordered_stages)

    figure, axis = plt.subplots(figsize=(9, 5))
    ordered_counts.plot(kind="bar", ax=axis, color="#805ad5")
    axis.set_title("Agent 턴 단계별 통과 건수 (진행 순서대로)")
    axis.set_ylabel("건수")
    return _save(figure, output_dir, "11_turn_stage_funnel.png")


def chart_turn_elapsed_by_stage(turn_events: pd.DataFrame, output_dir) -> str:
    plottable = turn_events[(turn_events["kind"] == "stage") & turn_events["elapsed_ms"].notna()]
    ordered_stages = [stage for stage in _STAGE_ORDER if stage in plottable["stage"].unique()]
    groups = [plottable.loc[plottable["stage"] == stage, "elapsed_ms"] for stage in ordered_stages]

    figure, axis = plt.subplots(figsize=(9, 6))
    axis.boxplot(groups, tick_labels=ordered_stages, showfliers=True)
    axis.set_title("Agent 턴 단계별 소요시간(ms) 분포")
    axis.set_ylabel("소요시간(ms)")
    axis.set_yscale("log")
    plt.setp(axis.get_xticklabels(), rotation=30, ha="right")
    return _save(figure, output_dir, "12_turn_elapsed_by_stage.png")


def chart_turn_outcome_over_time(turn_events: pd.DataFrame, output_dir) -> str:
    outcomes = turn_events[turn_events["kind"].isin(["done", "error"])]
    daily_outcome = outcomes.set_index("created_at").groupby([pd.Grouper(freq="1D"), "kind"]).size().unstack(fill_value=0)

    figure, axis = plt.subplots(figsize=(9, 5))
    daily_outcome.plot(kind="bar", stacked=True, ax=axis, color={"done": "#38a169", "error": "#e53e3e"})
    axis.set_title("일별 Agent 턴 완료/에러 건수")
    axis.set_ylabel("건수")
    return _save(figure, output_dir, "13_turn_outcome_over_time.png")


# ── 4. API 요청 지표 (request_metrics) ──────────────────────────────────


def chart_request_count_by_path(request_metrics: pd.DataFrame, output_dir) -> str:
    top_paths = request_metrics["path"].value_counts().head(15)
    figure, axis = plt.subplots(figsize=(9, 7))
    top_paths.sort_values().plot(kind="barh", ax=axis, color="#2b6cb0")
    axis.set_title("엔드포인트별 요청 건수 상위 15개")
    axis.set_xlabel("건수")
    return _save(figure, output_dir, "14_request_count_by_path.png")


def chart_request_latency_by_path(request_metrics: pd.DataFrame, output_dir) -> str:
    top_paths = request_metrics["path"].value_counts().head(10).index
    plottable = request_metrics[request_metrics["path"].isin(top_paths)]
    groups = [plottable.loc[plottable["path"] == path, "latency_ms"] for path in top_paths]

    figure, axis = plt.subplots(figsize=(10, 6))
    axis.boxplot(groups, tick_labels=top_paths, showfliers=True)
    axis.set_title("요청 건수 상위 10개 엔드포인트의 지연시간(ms) 분포")
    axis.set_ylabel("지연시간(ms)")
    axis.set_yscale("log")
    plt.setp(axis.get_xticklabels(), rotation=45, ha="right")
    return _save(figure, output_dir, "15_request_latency_by_path_boxplot.png")


def chart_request_hourly_heatmap(request_metrics: pd.DataFrame, output_dir) -> str:
    working = request_metrics.copy()
    working["hour"] = working["created_at"].dt.hour
    working["date"] = working["created_at"].dt.date
    pivot_table = working.pivot_table(index="date", columns="hour", values="id", aggfunc="count", fill_value=0)

    figure, axis = plt.subplots(figsize=(11, 6))
    image = axis.imshow(pivot_table.values, aspect="auto", cmap="YlOrRd")
    axis.set_yticks(range(len(pivot_table.index)))
    axis.set_yticklabels(pivot_table.index)
    axis.set_xticks(range(len(pivot_table.columns)))
    axis.set_xticklabels(pivot_table.columns)
    axis.set_title("날짜 x 시간대별 요청량 히트맵")
    axis.set_xlabel("시(hour, UTC)")
    figure.colorbar(image, ax=axis, label="요청 건수")
    return _save(figure, output_dir, "16_request_hourly_heatmap.png")


# ── 5. 평가 결과 (eval_runs.jsonl) ──────────────────────────────────────


def chart_eval_run_count_by_source(eval_runs: pd.DataFrame, output_dir) -> str:
    counts = eval_runs["source"].value_counts()
    figure, axis = plt.subplots(figsize=(8, 5))
    counts.plot(kind="bar", ax=axis, color="#319795")
    axis.set_title("평가 결과 소스(source)별 건수")
    axis.set_ylabel("건수")
    return _save(figure, output_dir, "17_eval_run_count_by_source.png")


def chart_ragas_metric_distribution(eval_runs: pd.DataFrame, output_dir) -> str:
    ragas_rows = eval_runs[eval_runs["source"] == "ragas"]
    metric_columns = [col for col in ragas_rows.columns if col.startswith("metric__")]
    if not metric_columns:
        return ""

    figure, axis = plt.subplots(figsize=(9, 5))
    axis.boxplot([ragas_rows[col].dropna() for col in metric_columns], tick_labels=[c.replace("metric__", "") for c in metric_columns])
    axis.set_title("RAGAS 평가(RAG 검색) 지표별 분포")
    axis.set_ylabel("점수(0~1)")
    plt.setp(axis.get_xticklabels(), rotation=20, ha="right")
    return _save(figure, output_dir, "18_ragas_metric_distribution.png")


def chart_chunk_experiment_metric_by_chunk_size(eval_runs: pd.DataFrame, output_dir) -> str:
    chunk_rows = eval_runs[eval_runs["source"] == "ragas_chunk_experiment"]
    if chunk_rows.empty or "metric__context_recall" not in chunk_rows.columns:
        return ""

    chunk_rows = chunk_rows.copy()
    chunk_rows["chunk_size"] = chunk_rows["agent_label"].str.extract(r"cs(\d+)_ov").astype(float)
    grouped = chunk_rows.groupby("chunk_size")[["metric__context_recall", "metric__answer_correctness", "metric__hit_at_5"]].mean()
    if grouped.empty:
        return ""

    figure, axis = plt.subplots(figsize=(9, 5))
    grouped.plot(kind="bar", ax=axis)
    axis.set_title("chunk_size별 평균 지표 (지금까지 저장된 만큼, 실험 진행 중)")
    axis.set_ylabel("점수(0~1)")
    axis.set_xlabel("chunk_size")
    return _save(figure, output_dir, "19_chunk_experiment_metric_by_chunk_size.png")


def chart_workflow_score_distribution(eval_runs: pd.DataFrame, output_dir) -> str:
    workflow_rows = eval_runs[eval_runs["source"].isin(["pm4py", "worfbench"])]
    if workflow_rows.empty:
        return ""

    figure, axis = plt.subplots(figsize=(8, 5))
    for source, color in [("pm4py", "#3182ce"), ("worfbench", "#dd6b20")]:
        scores = workflow_rows.loc[workflow_rows["source"] == source, "score"].dropna()
        if len(scores) > 0:
            axis.hist(scores, bins=20, alpha=0.5, label=source, color=color)
    axis.set_title("pm4py / WorFBench 점수 분포")
    axis.set_xlabel("score")
    axis.set_ylabel("건수")
    axis.legend()
    return _save(figure, output_dir, "20_workflow_score_distribution.png")
