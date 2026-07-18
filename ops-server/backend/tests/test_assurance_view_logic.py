import sys
import unittest
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
sys.path.insert(0, str(FRONTEND_DIR))

from views.assurance_records import _business_persisted_text, _status_text  # noqa: E402


class AssuranceViewLogicTest(unittest.TestCase):
    def test_integrity_failure_has_highest_priority(self):
        row = {
            "integrity_valid": False,
            "decision": "allow_candidate",
            "assurance_verdict": "observed",
        }

        self.assertEqual(_status_text(row), "무결성 실패")

    def test_unknown_integrity_never_looks_observed(self):
        row = {"decision": "allow_candidate", "assurance_verdict": "observed"}

        self.assertEqual(_status_text(row), "무결성 미확인")

    def test_only_known_allow_candidate_is_observed(self):
        base = {"integrity_valid": True, "assurance_verdict": "observed"}

        self.assertEqual(_status_text({**base, "decision": "allow_candidate"}), "관찰됨")
        self.assertEqual(_status_text({**base, "decision": "future_value"}), "판단 불가")

    def test_failure_states_remain_distinct(self):
        base = {"integrity_valid": True}

        self.assertEqual(
            _status_text({**base, "decision": "deny", "assurance_verdict": "deny"}),
            "계약 위반",
        )
        self.assertEqual(
            _status_text({**base, "decision": "unassured", "assurance_verdict": "refused"}),
            "보증 거절",
        )

    def test_unknown_business_persistence_is_not_false(self):
        self.assertEqual(_business_persisted_text({}), "미확인")
        self.assertEqual(_business_persisted_text({"business_persisted": False}), "미저장")


if __name__ == "__main__":
    unittest.main()
