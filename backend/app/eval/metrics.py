from collections.abc import Iterable

from .log_schema import EvalMetric, EvalRunRecord

FIXED_METRICS = (
    "pm4py_fitness",
    "pm4py_precision",
    "worfbench_precision",
    "worfbench_recall",
    "worfbench_f1_score",
)


def metrics_from_raw(source: str, raw: dict | None) -> list[EvalMetric]:
    if not raw:
        return []
    mapping = {
        "pm4py": (("pm4py_fitness", "fitness"), ("pm4py_precision", "precision")),
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
