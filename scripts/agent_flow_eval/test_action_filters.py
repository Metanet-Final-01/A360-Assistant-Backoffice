import unittest

from action_filters import (
    is_ambiguous_generic_action,
    is_control_flow_marker_action,
    is_session_lifecycle_action,
    matches_infrastructure_keyword,
    normalize_steps_for_evaluation,
    should_exclude_action,
)


class ActionFiltersTest(unittest.TestCase):
    def test_xml_sessions_remain_scoreable(self) -> None:
        self.assertFalse(is_session_lifecycle_action("XML", "startSession"))
        self.assertFalse(is_session_lifecycle_action("XML", "endSession"))

    def test_excel_close_actions_remain_scoreable(self) -> None:
        self.assertFalse(is_session_lifecycle_action("Excel", "CloseSpreadsheet"))
        self.assertFalse(is_session_lifecycle_action("Excel_MS", "CloseSpreadsheet"))
        self.assertFalse(
            is_session_lifecycle_action(
                "Microsoft 365 Excel package in Automation 360",
                "office365ExcelClose",
            )
        )

    def test_connection_and_browser_lifecycle_actions_are_excluded(self) -> None:
        self.assertTrue(is_session_lifecycle_action("Email", "emailConnect"))
        self.assertTrue(is_session_lifecycle_action("Gmail", "Disconnect"))
        self.assertTrue(is_session_lifecycle_action("Browser", "startSession"))
        self.assertTrue(
            is_session_lifecycle_action(
                "Microsoft 365 Excel package in Automation 360",
                "usingConnectActionInMicrosoft365ExcelPackage",
            )
        )

    def test_other_filters_are_package_scoped(self) -> None:
        self.assertTrue(is_control_flow_marker_action("If", "if"))
        self.assertFalse(is_control_flow_marker_action("String", "if"))

    def test_rule_based_conversion_keeps_potential_business_actions(self) -> None:
        steps = [
            {"type": "action", "package": "Logging", "action": "LogToFile"},
            {"type": "action", "package": "MessageBox", "action": "show"},
            {"type": "action", "package": "Screen", "action": "captureDesktop"},
            {"type": "action", "package": "Excel_MS", "action": "formatCell"},
            {"type": "action", "package": "String", "action": "assign"},
            {"type": "action", "package": "XML", "action": "startSession"},
            {"type": "action", "package": "Excel_MS", "action": "CloseSpreadsheet"},
            {
                "type": "container",
                "disabled": True,
                "steps": [{"type": "action", "package": "File", "action": "copyFiles"}],
            },
            {
                "type": "try",
                "steps": [{"type": "action", "package": "File", "action": "copyFiles"}],
                "branches": [
                    {
                        "branch": "catch",
                        "steps": [{"type": "action", "package": "File", "action": "deleteFiles"}],
                    },
                    {
                        "branch": "finally",
                        "steps": [{"type": "action", "package": "File", "action": "moveFiles"}],
                    },
                ],
            },
        ]

        converted = normalize_steps_for_evaluation(steps)

        self.assertEqual(
            [(step.get("package"), step.get("action")) for step in converted if step["type"] == "action"],
            [
                ("MessageBox", "show"),
                ("Screen", "captureDesktop"),
                ("Excel_MS", "formatCell"),
                ("String", "assign"),
                ("XML", "startSession"),
                ("Excel_MS", "CloseSpreadsheet"),
            ],
        )
        self.assertEqual(converted[-1]["branches"][0]["steps"], [])
        self.assertEqual(converted[-1]["branches"][1]["steps"][0]["action"], "moveFiles")
        self.assertFalse(should_exclude_action("MessageBox", "show"))
        self.assertFalse(should_exclude_action("Excel_MS", "formatCell"))

    def test_ambiguous_generic_actions_are_narrowly_scoped(self) -> None:
        self.assertTrue(is_ambiguous_generic_action("Folder", "createFolder"))
        self.assertTrue(is_ambiguous_generic_action("Folder", "deleteFolder"))
        self.assertTrue(is_ambiguous_generic_action("String", "assign"))
        self.assertTrue(is_ambiguous_generic_action("MessageBox", "messageBox"))
        # 명백히 업무 액션인 것들은 대상이 아님 - 이미 핵심업무로 취급됨
        self.assertFalse(is_ambiguous_generic_action("WebAutomation", "clickelement"))
        self.assertFalse(is_ambiguous_generic_action("Excel_MS", "SetCell"))
        self.assertFalse(is_ambiguous_generic_action("DataTable", "deleteRow"))

    def test_infrastructure_keyword_matches_only_log_related_paths(self) -> None:
        # 0098 실측: 로그 관련 변수명 - 전부 키워드로 잡힘
        self.assertTrue(matches_infrastructure_keyword("folderPath=$pStrLogsFolder$"))
        self.assertTrue(matches_infrastructure_keyword("folderPath=$pStrAuditLogFolder$"))
        self.assertTrue(matches_infrastructure_keyword("filePath=$pStrErrorLogFile$"))
        self.assertTrue(matches_infrastructure_keyword("folderPath=$pStrSnapshotsFolder$"))
        # 0098 실측: 진짜 업무 대상 - 키워드 없음, LLM으로 넘어가야 함
        self.assertFalse(matches_infrastructure_keyword("folderPath=$pStrPercentTemp$\\$pStrFolderName$"))
        self.assertFalse(matches_infrastructure_keyword("folderPath=$pStrWTemp$\\$pStrFolderName$"))
        # 0089 실측
        self.assertFalse(matches_infrastructure_keyword("folderPath=$iStrArchieveFolderPath$"))


if __name__ == "__main__":
    unittest.main()
