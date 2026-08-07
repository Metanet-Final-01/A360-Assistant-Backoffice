import unittest

from app.eval.log_schema import EvalMetric, EvalRunRecord
from app.eval.metrics import metrics_from_raw, paired_metric_values


def run(case_id: str, label: str, metric: str, value: float) -> EvalRunRecord:
    return EvalRunRecord(
        case_id=case_id,
        source="manual",
        agent_label=label,
        metrics=[EvalMetric(name=metric, value=value)],
    )


class EvalMetricsTest(unittest.TestCase):
    def test_only_pairs_same_case_and_metric(self):
        a = [run("case-1", "a", "workflow_rule_only_f1", 0.4), run("case-2", "a", "workflow_rule_only_f1", 0.9)]
        b = [run("case-1", "b", "workflow_rule_only_f1", 0.8), run("case-3", "b", "workflow_rule_only_f1", 0.1)]
        paired = paired_metric_values(a, b)
        self.assertEqual(paired["workflow_rule_only_f1"], [("case-1", 0.4, 0.8)])

    def test_repeated_case_is_averaged_before_pairing(self):
        a = [run("case-1", "a", "workflow_rule_only_f1", 0.2), run("case-1", "a", "workflow_rule_only_f1", 0.6)]
        b = [run("case-1", "b", "workflow_rule_only_f1", 0.9)]
        self.assertEqual(paired_metric_values(a, b)["workflow_rule_only_f1"], [("case-1", 0.4, 0.9)])

    def test_raw_metrics_keep_zero(self):
        metrics = metrics_from_raw("workflow", {"case_id": "x", "rule_only_f1": 0.0, "rule_only_precision": 0.5})
        self.assertEqual({item.name: item.value for item in metrics}, {"workflow_rule_only_f1": 0.0, "workflow_rule_only_precision": 0.5})


if __name__ == "__main__":
    unittest.main()
