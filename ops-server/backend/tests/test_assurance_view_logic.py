import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
sys.path.insert(0, str(FRONTEND_DIR))

from views.assurance_records import (  # noqa: E402
    _business_persisted_text,
    _change_group_key,
    _change_control_rows,
    _change_subject,
    _current_change_record,
    _current_status_text,
    _fetch,
    _format_kst,
    _group_change_records,
    _get,
    _human_review_summary,
    _human_review_text,
    _latest_change_rows,
    _pr_summary_rows,
    _reconcile_pr_selection,
    _render_detail,
    _status_notice,
    _status_text,
    _table_rows,
    _timeline_choices,
    _timeline_rows,
)


class AssuranceViewLogicTest(unittest.TestCase):
    @patch("views.assurance_records.st.session_state", new_callable=dict)
    def test_stale_pr_selection_moves_to_first_valid_option(self, session_state):
        session_state["assurance_selected_pr"] = "org/repo#41"

        selected = _reconcile_pr_selection(["org/repo#42", "org/repo#43"])

        self.assertEqual(selected, "org/repo#42")
        self.assertEqual(session_state["assurance_selected_pr"], "org/repo#42")

    @patch("views.assurance_records.st.session_state", new_callable=dict)
    def test_valid_pr_selection_is_preserved(self, session_state):
        session_state["assurance_selected_pr"] = "org/repo#42"

        selected = _reconcile_pr_selection(["org/repo#42"])

        self.assertEqual(selected, "org/repo#42")
        self.assertEqual(session_state["assurance_selected_pr"], "org/repo#42")

    @patch("views.assurance_records.st.session_state", new_callable=dict)
    def test_pr_selection_is_removed_when_no_options_remain(self, session_state):
        session_state["assurance_selected_pr"] = "org/repo#42"

        selected = _reconcile_pr_selection([])

        self.assertIsNone(selected)
        self.assertNotIn("assurance_selected_pr", session_state)

    def test_record_table_uses_kst_display_time(self):
        rows = _table_rows([{
            "created_at": "2026-07-22T02:16:00+00:00",
            "integrity_valid": True,
            "decision": "allow_candidate",
            "assurance_verdict": "observed",
        }])

        self.assertEqual(rows[0]["시각"], "2026-07-22 11:16:00 KST")

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

    def test_current_status_turns_contract_state_into_operator_action(self):
        base = {"integrity_valid": True}

        self.assertEqual(
            _current_status_text({
                **base,
                "decision": "allow_candidate",
                "assurance_verdict": "observed",
            }),
            "통과 (Observe)",
        )
        self.assertEqual(
            _current_status_text({
                **base,
                "decision": "unassured",
                "assurance_verdict": "refused",
            }),
            "검토 필요",
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

    def test_human_review_status_distinguishes_current_and_stale_approval(self):
        approved = {
            "human_review": {
                "status": "approved",
                "reason_code": "HUMAN_REVIEW_VERIFIED",
                "review": {
                    "reviewer_login": "reviewer",
                    "submitted_at": "2026-07-21T08:00:00Z",
                    "commit_id": "a" * 40,
                },
            }
        }
        self.assertEqual(_human_review_text(approved), "검토 완료")
        self.assertEqual(_human_review_summary(approved)["승인자"], "reviewer")
        self.assertEqual(
            _human_review_summary(approved)["승인 시각"],
            "2026-07-21 17:00:00 KST",
        )
        self.assertEqual(
            _human_review_text({"human_review": {"status": "stale"}}),
            "재검토 필요",
        )

    def test_missing_review_summary_explains_pre_approval_record(self):
        summary = _human_review_summary({
            "integrity_valid": True,
            "decision": "unassured",
            "assurance_verdict": "refused",
            "human_review": {
                "status": "missing",
                "reason_code": "HUMAN_REVIEW_NOT_SUBMITTED",
                "review": None,
            }
        })

        self.assertEqual(summary["승인자"], "승인 전 기록")
        self.assertEqual(summary["승인 시각"], "승인 전 기록")
        self.assertEqual(
            summary["상태 사유"],
            "이 기록 생성 시점에는 사람 승인이 없었음",
        )

    def test_missing_review_is_not_required_for_observed_candidate(self):
        row = {
            "integrity_valid": True,
            "decision": "allow_candidate",
            "assurance_verdict": "observed",
            "human_review": {
                "status": "missing",
                "reason_code": "HUMAN_REVIEW_NOT_SUBMITTED",
                "review": None,
            },
        }

        self.assertEqual(_human_review_text(row), "검토 불필요")
        summary = _human_review_summary(row)
        self.assertEqual(summary["승인자"], "-")
        self.assertEqual(
            summary["상태 사유"],
            "보호 대상 변경이 없어 별도 승인 증거가 필요하지 않음",
        )

    def test_current_record_keeps_same_head_approval_after_plain_rerun(self):
        head_sha = "a" * 40
        common = {
            "harness": "change",
            "integrity_valid": True,
            "change_subject": {
                "repository": "org/repo",
                "pull_request_number": 42,
                "head_sha": head_sha,
            },
        }
        approved = {
            **common,
            "created_at": "2026-07-21T00:02:00Z",
            "receipt_digest": "sha256:approved",
            "decision": "allow_candidate",
            "assurance_verdict": "observed",
            "human_review": {
                "status": "approved",
                "review": {
                    "reviewer_login": "reviewer",
                    "submitted_at": "2026-07-21T00:01:00Z",
                    "commit_id": head_sha,
                },
            },
        }
        plain_rerun = {
            **common,
            "created_at": "2026-07-21T00:03:00Z",
            "receipt_digest": "sha256:rerun",
            "decision": "unassured",
            "assurance_verdict": "refused",
            "human_review": {"status": "missing", "review": None},
        }

        current = _current_change_record([approved, plain_rerun])

        self.assertEqual(current["receipt_digest"], "sha256:approved")
        self.assertEqual(_human_review_summary(current)["승인자"], "reviewer")

    def test_current_record_does_not_carry_approval_to_new_head(self):
        old_head = "a" * 40
        new_head = "b" * 40
        approved = {
            "harness": "change",
            "created_at": "2026-07-21T00:02:00Z",
            "receipt_digest": "sha256:approved",
            "change_subject": {"head_sha": old_head},
            "human_review": {"status": "approved"},
        }
        new_commit = {
            "harness": "change",
            "created_at": "2026-07-21T00:03:00Z",
            "receipt_digest": "sha256:new-head",
            "change_subject": {"head_sha": new_head},
            "human_review": {"status": "missing"},
        }

        current = _current_change_record([approved, new_commit])

        self.assertEqual(current["receipt_digest"], "sha256:new-head")

    def test_current_record_honors_dismissal_after_approval(self):
        head_sha = "a" * 40
        approved = {
            "created_at": "2026-07-21T00:02:00Z",
            "receipt_digest": "sha256:approved",
            "change_subject": {"head_sha": head_sha},
            "human_review": {"status": "approved"},
        }
        dismissed = {
            "created_at": "2026-07-21T00:03:00Z",
            "receipt_digest": "sha256:dismissed",
            "change_subject": {"head_sha": head_sha},
            "human_review": {"status": "dismissed"},
        }

        current = _current_change_record([approved, dismissed])

        self.assertEqual(current["receipt_digest"], "sha256:dismissed")

    def test_human_review_can_be_read_from_detail_payload(self):
        detail = {"receipt_payload": {"human_review": {"status": "dismissed"}}}
        self.assertEqual(_human_review_text(detail), "승인 취소")

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

    def test_change_records_group_by_repository_and_pr_across_commits(self):
        rows = [
            {
                "harness": "change",
                "created_at": "2026-07-21T00:02:00Z",
                "receipt_digest": "sha256:second",
                "change_subject": {
                    "repository": "org/repo",
                    "pull_request_number": 42,
                    "head_sha": "b" * 40,
                },
            },
            {
                "harness": "output",
                "created_at": "2026-07-21T00:03:00Z",
                "receipt_digest": "sha256:output",
            },
            {
                "harness": "change",
                "created_at": "2026-07-21T00:01:00Z",
                "receipt_digest": "sha256:first",
                "change_subject": {
                    "repository": "org/repo",
                    "pull_request_number": 42,
                    "head_sha": "a" * 40,
                },
            },
        ]

        groups, ungrouped = _group_change_records(rows)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0], ("org/repo", 42))
        self.assertEqual(
            [row["receipt_digest"] for row in groups[0][1]],
            ["sha256:first", "sha256:second"],
        )
        self.assertEqual(ungrouped, [rows[1]])
        self.assertEqual(_change_group_key(rows[0]), ("org/repo", 42))

    def test_timeline_keeps_pre_and_post_approval_records_append_only(self):
        common = {
            "harness": "change",
            "integrity_valid": True,
            "decision": "unassured",
            "assurance_verdict": "refused",
        }
        rows = [
            {
                **common,
                "created_at": "2026-07-21T00:01:00Z",
                "receipt_digest": "sha256:before",
                "change_subject": {
                    "repository": "org/repo",
                    "pull_request_number": 42,
                    "head_sha": "a" * 40,
                    "source_event": "pull_request",
                },
                "human_review": {"status": "missing"},
            },
            {
                **common,
                "created_at": "2026-07-21T00:02:00Z",
                "receipt_digest": "sha256:approved",
                "change_subject": {
                    "repository": "org/repo",
                    "pull_request_number": 42,
                    "head_sha": "a" * 40,
                    "source_event": "pull_request_review",
                },
                "human_review": {
                    "status": "approved",
                    "review": {
                        "reviewer_login": "reviewer",
                        "submitted_at": "2026-07-21T00:01:30Z",
                    },
                },
            },
            {
                **common,
                "created_at": "2026-07-21T00:03:00Z",
                "receipt_digest": "sha256:new-head",
                "change_subject": {
                    "repository": "org/repo",
                    "pull_request_number": 42,
                    "head_sha": "b" * 40,
                    "source_event": "pull_request",
                },
                "human_review": {"status": "missing"},
            },
        ]

        timeline = _timeline_rows(rows)

        self.assertEqual([item["기록 지문"] for item in timeline], [
            "sha256:before",
            "sha256:approved",
            "sha256:new-head",
        ])
        self.assertEqual(
            [item["단계"] for item in timeline],
            ["승인 전 검사", "사람 승인 반영", "새 커밋 검사"],
        )
        self.assertEqual(timeline[1]["승인자"], "reviewer")
        self.assertEqual(timeline[0]["시각"], "2026-07-21 09:01:00 KST")

    def test_invalid_or_missing_timestamp_is_safe(self):
        self.assertEqual(_format_kst(None), "-")
        self.assertEqual(_format_kst("not-a-time"), "not-a-time")

    def test_timeline_choices_do_not_drop_duplicate_display_labels(self):
        repeated_prefix = "sha256:same-prefix"
        first = {
            "harness": "change",
            "created_at": "2026-07-21T00:01:00Z",
            "receipt_digest": f"{repeated_prefix}-first",
            "change_subject": {
                "repository": "org/repo",
                "pull_request_number": 42,
                "head_sha": "a" * 40,
            },
            "human_review": {"status": "missing"},
        }
        second = {
            **first,
            "receipt_digest": f"{repeated_prefix}-second",
        }

        choices = _timeline_choices([first, second])

        self.assertEqual(len(choices), 2)
        self.assertNotEqual(choices[0][0], choices[1][0])
        self.assertEqual(choices[0][1], choices[1][1])
        self.assertEqual(
            {token for token, _label, _row in choices},
            {first["receipt_digest"], second["receipt_digest"]},
        )

    def test_pr_summary_uses_only_latest_append_only_record_as_current(self):
        before = {
            "harness": "change",
            "created_at": "2026-07-21T00:01:00Z",
            "receipt_digest": "sha256:before",
            "integrity_valid": True,
            "decision": "unassured",
            "assurance_verdict": "refused",
            "change_subject": {
                "repository": "org/repo",
                "pull_request_number": 42,
                "head_sha": "a" * 40,
            },
            "human_review": {"status": "missing"},
        }
        after = {
            **before,
            "created_at": "2026-07-21T00:02:00Z",
            "receipt_digest": "sha256:after",
            "decision": "allow_candidate",
            "assurance_verdict": "observed",
            "human_review": {
                "status": "approved",
                "review": {
                    "reviewer_login": "reviewer",
                    "submitted_at": "2026-07-21T00:01:30Z",
                    "commit_id": "a" * 40,
                },
            },
        }
        groups = [(("org/repo", 42), [before, after])]

        self.assertEqual(_latest_change_rows(groups), [after])
        summary = _pr_summary_rows(groups)[0]
        self.assertEqual(summary["현재 상태"], "통과 (Observe)")
        self.assertEqual(summary["사람 검토"], "검토 완료")
        self.assertEqual(summary["감사 기록"], 2)
        self.assertEqual(summary["승인 후속 기록"], "있음")

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

    @patch("views.assurance_records._status_text", return_value="표시 문구 변경")
    def test_change_notice_uses_raw_contract_not_display_label(self, status_text):
        level, message = _status_notice({
            "harness": "change",
            "integrity_valid": True,
            "decision": "unassured",
            "assurance_verdict": "refused",
            "rollout_mode": "observe",
            "enforcement_effect": "none",
        })

        self.assertEqual(level, "warning")
        self.assertIn("병합을 자동 차단하지 않습니다", message)
        status_text.assert_called_once()

    @patch("views.assurance_records.section_header")
    @patch("views.assurance_records.st.json")
    @patch("views.assurance_records.st.dataframe")
    @patch("views.assurance_records.st.info")
    @patch("views.assurance_records.st.warning")
    @patch("views.assurance_records.st.columns")
    @patch("views.assurance_records._get")
    def test_change_detail_renders_subject_and_control_table(
        self, get, columns, warning, info, dataframe, json, section_header
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
                "human_review": {
                    "status": "missing",
                    "reason_code": "HUMAN_REVIEW_NOT_SUBMITTED",
                    "review": None,
                },
            },
        }

        _render_detail({"receipt_digest": "sha256:" + "a" * 64})

        left_payload = left.json.call_args.args[0]
        self.assertEqual(left_payload["pull_request_number"], 292)
        self.assertNotIn("request_id", left_payload)
        rendered = dataframe.call_args.args[0]
        self.assertEqual(rendered.iloc[0]["통제"], "CH-06")
        self.assertEqual(rendered.iloc[0]["상태"], "추가 검토 필요")
        self.assertEqual(warning.call_count, 1)
        messages = [call.args[0] for call in warning.call_args_list]
        self.assertTrue(any("병합을 자동 차단하지 않습니다" in message for message in messages))
        info.assert_called_once()
        self.assertIn("승인 전에 생성된 과거 기록", info.call_args.args[0])

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
