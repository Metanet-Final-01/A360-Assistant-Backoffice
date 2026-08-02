import unittest

from convert_backend_recommendation import count_actions


class CountActionsTest(unittest.TestCase):
    def test_nested_actions(self) -> None:
        steps = [{"type": "container", "steps": [
            {"type": "action"},
            {"type": "container", "steps": [{"type": "action"}]},
        ]}]
        self.assertEqual(count_actions(steps), 2)


if __name__ == "__main__":
    unittest.main()
