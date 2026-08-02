import unittest

from normalize_extracted_workflows import normalize_workflow


class NormalizeExtractedWorkflowsTest(unittest.TestCase):
    def test_applies_the_same_narrow_conversion_rules(self) -> None:
        raw = {
            "nodes": [
                {"packageName": "Comment", "commandName": "comment"},
                {"packageName": "LogToFile", "commandName": "log"},
                {"packageName": "MessageBox", "commandName": "show"},
                {"packageName": "Excel_MS", "commandName": "formatCell"},
                {"packageName": "XML", "commandName": "startSession"},
                {
                    "packageName": "Step",
                    "commandName": "step",
                    "disabled": True,
                    "children": [{"packageName": "File", "commandName": "copyFiles"}],
                },
            ]
        }

        normalized = normalize_workflow(raw, "test.json")

        self.assertEqual(
            [(step["package"], step["action"]) for step in normalized["steps"]],
            [
                ("MessageBox", "show"),
                ("Excel_MS", "formatCell"),
                ("XML", "startSession"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
