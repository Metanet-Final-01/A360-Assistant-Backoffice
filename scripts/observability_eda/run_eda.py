"""관측 DB + 평가 결과 로그 전체를 대상으로 EDA를 실행하고, 그래프와 요약을
docs/local/eda_2026-07-19_charts/ 밑에 저장한다.

실행: ops-server/.venv/Scripts/python.exe scripts/observability_eda/run_eda.py
"""

import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import charts
import fetch_data
import ml_analysis

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "local" / "eda_2026-07-19_charts"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("데이터 가져오는 중...")
    llm_usage = fetch_data.fetch_llm_usage()
    rag_events = fetch_data.fetch_rag_events()
    turn_events = fetch_data.fetch_turn_events()
    request_metrics = fetch_data.fetch_request_metrics()
    eval_runs = fetch_data.load_eval_runs()
    print(f"  llm_usage={len(llm_usage)}, rag_events={len(rag_events)}, "
          f"turn_events={len(turn_events)}, request_metrics={len(request_metrics)}, eval_runs={len(eval_runs)}")

    generated_charts: list[str] = []

    print("LLM 사용량 그래프 그리는 중...")
    generated_charts.append(charts.chart_llm_cost_by_component(llm_usage, OUTPUT_DIR))
    generated_charts.append(charts.chart_llm_request_count_by_component(llm_usage, OUTPUT_DIR))
    generated_charts.append(charts.chart_llm_latency_by_component(llm_usage, OUTPUT_DIR))
    generated_charts.append(charts.chart_llm_cost_over_time(llm_usage, OUTPUT_DIR))
    generated_charts.append(charts.chart_llm_tokens_by_component(llm_usage, OUTPUT_DIR))

    print("RAG 파이프라인 그래프 그리는 중...")
    generated_charts.append(charts.chart_rag_event_duration_boxplot(rag_events, OUTPUT_DIR))
    generated_charts.append(charts.chart_rag_event_p95_bar(rag_events, OUTPUT_DIR))
    generated_charts.append(charts.chart_rag_hybrid_search_over_time(rag_events, OUTPUT_DIR))
    generated_charts.append(charts.chart_rag_error_rate_by_event(rag_events, OUTPUT_DIR))
    generated_charts.append(charts.chart_rag_volume_over_time(rag_events, OUTPUT_DIR))

    print("Agent 턴 그래프 그리는 중...")
    generated_charts.append(charts.chart_turn_stage_funnel(turn_events, OUTPUT_DIR))
    generated_charts.append(charts.chart_turn_elapsed_by_stage(turn_events, OUTPUT_DIR))
    generated_charts.append(charts.chart_turn_outcome_over_time(turn_events, OUTPUT_DIR))

    print("API 요청 그래프 그리는 중...")
    generated_charts.append(charts.chart_request_count_by_path(request_metrics, OUTPUT_DIR))
    generated_charts.append(charts.chart_request_latency_by_path(request_metrics, OUTPUT_DIR))
    generated_charts.append(charts.chart_request_hourly_heatmap(request_metrics, OUTPUT_DIR))

    print("평가 결과 그래프 그리는 중...")
    generated_charts.append(charts.chart_eval_run_count_by_source(eval_runs, OUTPUT_DIR))
    generated_charts.append(charts.chart_ragas_metric_distribution(eval_runs, OUTPUT_DIR))
    generated_charts.append(charts.chart_chunk_experiment_metric_by_chunk_size(eval_runs, OUTPUT_DIR))
    generated_charts.append(charts.chart_workflow_score_distribution(eval_runs, OUTPUT_DIR))

    print("ML 분석 실행 중 (이상치 탐지)...")
    anomaly_result = ml_analysis.detect_latency_anomalies(rag_events, OUTPUT_DIR)
    generated_charts.append(anomaly_result["chart_path"])

    print("ML 분석 실행 중 (느린 요청 예측)...")
    slow_prediction_result = ml_analysis.predict_slow_requests(rag_events, OUTPUT_DIR)
    generated_charts.append(slow_prediction_result["chart_path"])

    generated_charts = [path for path in generated_charts if path]
    print(f"\n그래프 {len(generated_charts)}개 저장 완료: {OUTPUT_DIR}")

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "row_counts": {
            "llm_usage": len(llm_usage), "rag_events": len(rag_events),
            "turn_events": len(turn_events), "request_metrics": len(request_metrics),
            "eval_runs": len(eval_runs),
        },
        "chart_count": len(generated_charts),
        "latency_anomaly_detection": anomaly_result["per_event_summary"],
        "slow_request_prediction": {
            "auc": slow_prediction_result["auc"],
            "baseline_positive_rate": slow_prediction_result["baseline_positive_rate"],
            "top_features": slow_prediction_result["top_features"],
            "interpretation": slow_prediction_result["interpretation"],
        },
    }
    summary_path = OUTPUT_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"요약 데이터 저장: {summary_path}")


if __name__ == "__main__":
    main()
