import unittest

from action_filters import (
    is_control_flow_marker_action,
    is_formatting_only_action,
    is_session_lifecycle_action,
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

    def test_other_filters_are_package_scoped(self) -> None:
        self.assertTrue(is_control_flow_marker_action("If", "if"))
        self.assertFalse(is_control_flow_marker_action("String", "if"))
        self.assertTrue(is_formatting_only_action("Excel_MS", "formatCell"))
        self.assertFalse(is_formatting_only_action("String", "formatCell"))


if __name__ == "__main__":
    unittest.main()
