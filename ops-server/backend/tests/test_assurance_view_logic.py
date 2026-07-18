import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
sys.path.insert(0, str(FRONTEND_DIR))

from views.assurance_records import (  # noqa: E402
    _business_persisted_text,
    _fetch,
    _get,
    _status_text,
)


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

    @patch("views.assurance_records.st.warning")
    @patch("views.assurance_records.requests.get")
    def test_detail_not_found_uses_record_message(self, get, warning):
        get.return_value = Mock(status_code=404)

        result = _get(
            "/assurance/records/sha256:" + "a" * 64,
            not_found_message="해당 검증 판정 기록을 찾을 수 없습니다.",
        )

        self.assertIsNone(result)
        warning.assert_called_once_with("해당 검증 판정 기록을 찾을 수 없습니다.")

    @patch("views.assurance_records._get", return_value=None)
    def test_failed_fetch_reports_failure_to_caller(self, get):
        self.assertFalse(_fetch({"since": "2026-07-01T00:00:00+00:00"}, append=False))
        get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
