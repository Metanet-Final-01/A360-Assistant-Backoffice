import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
sys.path.insert(0, str(FRONTEND_DIR))

from views.assurance_records import (  # noqa: E402
    _business_persisted_text,
    _change_control_rows,
    _change_subject,
    _fetch,
    _get,
    _render_detail,
    _status_notice,
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
            "보증 불충족",
        )

    def test_unknown_business_persistence_is_not_false(self):
        self.assertEqual(_business_persisted_text({}), "미확인")
        self.assertEqual(_business_persisted_text({"business_persisted": False}), "미저장")

    def test_change_controls_are_rendered_with_human_labels(self):
        rows = _change_control_rows({
            "controls": [
                {
                    "control_id": "CH-04",
                    "status": "unassured",
                    "reason_code": "DEPENDENCY_EVIDENCE_INCOMPLETE",
                    "evidence_uri": "evidence/dependency.json",
                    "evidence_digest": "sha256:abc",
                },
                "invalid",
            ]
        })

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["통제"], "CH-04")
        self.assertEqual(rows[0]["상태"], "추가 검토 필요")
        self.assertEqual(rows[0]["판정 설명"], "의존성 취약점·라이선스 증거가 부족함")
        self.assertEqual(rows[0]["사유 코드"], "DEPENDENCY_EVIDENCE_INCOMPLETE")

    def test_change_subject_uses_workflow_context(self):
        subject = _change_subject({
            "subject": {
                "repository": "org/repo",
                "pull_request_number": 292,
                "workflow_run_id": 123,
                "base_sha": "base",
                "head_sha": "head",
            },
            "provenance": {"workflow_name": "Change Assurance"},
        })

        self.assertEqual(subject["repository"], "org/repo")
        self.assertEqual(subject["pull_request_number"], 292)
        self.assertEqual(subject["workflow_name"], "Change Assurance")
        self.assertNotIn("request_id", subject)

    def test_change_refusal_explains_observe_is_not_merge_blocking(self):
        level, message = _status_notice({
            "harness": "change",
            "integrity_valid": True,
            "decision": "unassured",
            "assurance_verdict": "refused",
            "rollout_mode": "observe",
            "enforcement_effect": "none",
        })

        self.assertEqual(level, "warning")
        self.assertIn("추가 검토", message)
        self.assertIn("병합을 자동 차단하지 않습니다", message)

    @patch("views.assurance_records.section_header")
    @patch("views.assurance_records.st.json")
    @patch("views.assurance_records.st.dataframe")
    @patch("views.assurance_records.st.warning")
    @patch("views.assurance_records.st.columns")
    @patch("views.assurance_records._get")
    def test_change_detail_renders_subject_and_control_table(
        self, get, columns, warning, dataframe, json, section_header
    ):
        left, right = Mock(), Mock()
        columns.return_value = (left, right)
        get.return_value = {
            "harness": "change",
            "integrity_valid": True,
            "decision": "unassured",
            "assurance_verdict": "refused",
            "rollout_mode": "observe",
            "enforcement_effect": "none",
            "receipt_payload": {
                "subject": {
                    "repository": "org/repo",
                    "pull_request_number": 292,
                    "workflow_run_id": 123,
                },
                "provenance": {"workflow_name": "Change Assurance"},
                "controls": [{
                    "control_id": "CH-06",
                    "status": "unassured",
                    "reason_code": "PROTECTED_ORACLE_REVIEW_REQUIRED",
                }],
            },
        }

        _render_detail({"receipt_digest": "sha256:" + "a" * 64})

        left_payload = left.json.call_args.args[0]
        self.assertEqual(left_payload["pull_request_number"], 292)
        self.assertNotIn("request_id", left_payload)
        rendered = dataframe.call_args.args[0]
        self.assertEqual(rendered.iloc[0]["통제"], "CH-06")
        self.assertEqual(rendered.iloc[0]["상태"], "추가 검토 필요")
        warning.assert_called_once()
        self.assertIn("병합을 자동 차단하지 않습니다", warning.call_args.args[0])

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
