from collections.abc import Iterable

from .log_schema import EvalMetric, EvalRunRecord

# workflow_* 는 scripts/agent_flow_eval의 채점기(audit_final_goldset.py)가 내는 지표다.
# rule_only_* 가 공식 기준선(LLM 없이 항상 같은 값), judge_* 는 LLM 동치 판정까지 포함한
# 참고치, chain_* 은 순서까지 반영한 값이다. worfbench_* 는 외부 벤치마크 참고치로만 병기한다.
FIXED_METRICS = (
    "workflow_rule_only_precision",
    "workflow_rule_only_recall",
    "workflow_rule_only_f1",
    "workflow_judge_f1",
    "workflow_chain_f1",
    "workflow_branch_coverage",
    "workflow_branch_score",
    "worfbench_precision",
    "worfbench_recall",
    "worfbench_f1_score",
)


def metrics_from_raw(source: str, raw: dict | None) -> list[EvalMetric]:
    if not raw:
        return []
    mapping = {
        "workflow": (
            ("workflow_rule_only_precision", "rule_only_precision"),
            ("workflow_rule_only_recall", "rule_only_recall"),
            ("workflow_rule_only_f1", "rule_only_f1"),
            ("workflow_judge_f1", "judge_f1"),
            ("workflow_chain_f1", "chain_f1"),
            ("workflow_branch_coverage", "branch_coverage"),
            ("workflow_branch_score", "branch_score"),
        ),
        "worfbench": (
            ("worfbench_precision", "precision"),
            ("worfbench_recall", "recall"),
            ("worfbench_f1_score", "f1_score"),
        ),
    }
    return [EvalMetric(name=name, value=raw[key]) for name, key in mapping.get(source, ()) if raw.get(key) is not None]


def metrics_of(record: EvalRunRecord) -> dict[str, float]:
    return {metric.name: metric.value for metric in record.metrics if metric.name in FIXED_METRICS}


def paired_metric_values(
    runs_a: Iterable[EvalRunRecord], runs_b: Iterable[EvalRunRecord]
) -> dict[str, list[tuple[str, float, float]]]:
    """지표별로 A/B 모두 값이 있는 case_id만 짝지어 반환한다."""

    def by_case(runs: Iterable[EvalRunRecord]) -> dict[str, dict[str, float]]:
        values: dict[str, dict[str, list[float]]] = {}
        for run in runs:
            bucket = values.setdefault(run.case_id, {})
            for name, value in metrics_of(run).items():
                bucket.setdefault(name, []).append(value)
        return {
            case_id: {name: sum(items) / len(items) for name, items in metrics.items()}
            for case_id, metrics in values.items()
        }

    a_by_case, b_by_case = by_case(runs_a), by_case(runs_b)
    paired: dict[str, list[tuple[str, float, float]]] = {}
    for case_id in sorted(set(a_by_case) & set(b_by_case)):
        for metric in FIXED_METRICS:
            if metric in a_by_case[case_id] and metric in b_by_case[case_id]:
                paired.setdefault(metric, []).append(
                    (case_id, a_by_case[case_id][metric], b_by_case[case_id][metric])
                )
    return paired
