import unittest

from action_matching import (
    canonicalize_action,
    load_action_equivalence_map,
    load_conditional_equivalence_groups,
)
from adapters.pm4py_adapter import _canonical_label, load_action_equivalence_map as load_adapter_equivalence_map


class ActionEquivalenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plain = load_action_equivalence_map()
        cls.conditional = load_conditional_equivalence_groups()

    def canonical(self, package: str, action: str) -> str:
        return canonicalize_action(package, action, {}, self.plain, self.conditional)

    def test_catalog_aliases_and_label_normalization(self) -> None:
        self.assertEqual(self.canonical("Gmail", "send"), "Email.cloudUsingSendAction")
        self.assertEqual(self.canonical("Locale", "datetostring"), "Datetime.cloudConvertDatetimeToString")
        self.assertEqual(
            self.canonical("Data Table", "dataTablePackageDeleteRowAction"),
            "DataTable.deleteRow",
        )
        self.assertEqual(
            self.canonical("Data Table", "dataTablePackageInsertRowAction"),
            "DataTable.insertRow",
        )

    def test_adapter_uses_same_label_normalization(self) -> None:
        mapping = load_adapter_equivalence_map()
        self.assertEqual(_canonical_label("Gmail", "send", mapping), "Email.cloudUsingSendAction")

    def test_unregistered_labels_ignore_case_and_spaces(self) -> None:
        self.assertEqual(
            self.canonical("Unlisted Package", "Do Thing"),
            self.canonical("unlistedpackage", "dothing"),
        )

    def test_excel_close_aliases_share_one_canonical_action(self) -> None:
        expected = self.canonical("Excel_MS", "CloseSpreadsheet")
        self.assertEqual(self.canonical("Excel", "CloseSpreadsheet"), expected)
        self.assertEqual(
            self.canonical(
                "Microsoft 365 Excel package in Automation 360",
                "office365ExcelClose",
            ),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
