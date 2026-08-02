import unittest

from convert_backend_recommendation import (
    actions_to_steps,
    convert_recommendation,
    count_actions,
    find_recommendation_step,
)


class CountActionsTest(unittest.TestCase):
    def test_finds_recommendation_from_analyze_when_recommend_is_plain_answer(self) -> None:
        manifest = {"steps": [
            {
                "name": "turnAnalyze",
                "response": {"final_event": {"data": {"recommendation": {"steps": [{}]}}}},
            },
            {
                "name": "turnRecommend",
                "response": {"final_event": {"data": {"type": "answer"}}},
            },
        ]}

        self.assertEqual(find_recommendation_step(manifest)["name"], "turnAnalyze")

    def test_nested_actions(self) -> None:
        steps = [{"type": "container", "steps": [
            {"type": "action"},
            {"type": "container", "steps": [{"type": "action"}]},
        ]}]
        self.assertEqual(count_actions(steps), 2)

    def test_preserves_if_else_loop_and_try_structure(self) -> None:
        actions = [
            {"package": "If", "action": "if", "children": [{"package": "File", "action": "copyFiles"}]},
            {"package": "If", "action": "else", "children": [{"package": "File", "action": "deleteFiles"}]},
            {
                "package": "Error handler",
                "action": "Try",
                "children": [
                    {
                        "package": "Loop",
                        "action": "loop.commands.start",
                        "children": [{"package": "Folder", "action": "createFolder"}],
                    }
                ],
            },
            {"package": "Error handler", "action": "Catch", "children": [{"package": "Screen", "action": "captureDesktop"}]},
            {"package": "Error handler", "action": "Finally", "children": [{"package": "XML", "action": "endSession"}]},
        ]

        steps = actions_to_steps(actions)

        self.assertEqual([step["type"] for step in steps], ["if", "try"])
        self.assertEqual(steps[0]["branches"][0]["branch"], "else")
        self.assertEqual(steps[1]["steps"][0]["type"], "loop")
        self.assertEqual([branch["branch"] for branch in steps[1]["branches"]], ["catch", "finally"])
        self.assertEqual(count_actions(steps), 5)

    def test_applies_common_rules_during_conversion(self) -> None:
        converted = convert_recommendation(
            run_manifest={"run_id": "test"},
            data={},
            recommendation={
                "steps": [
                    {
                        "name": "work",
                        "actions": [
                            {"package": "Logging", "action": "LogToFile"},
                            {"package": "MessageBox", "action": "show"},
                            {"package": "Excel_MS", "action": "formatCell"},
                        ],
                    }
                ]
            },
            source_file="test.json",
            step_name="turnRecommend",
        )

        actions = converted["steps"][0]["steps"]
        self.assertEqual(
            [(action["package"], action["action"]) for action in actions],
            [("MessageBox", "show"), ("Excel_MS", "formatCell")],
        )


if __name__ == "__main__":
    unittest.main()
