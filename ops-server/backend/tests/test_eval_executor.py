import unittest
from unittest.mock import patch

from app.eval import executor


class EvalExecutorTest(unittest.TestCase):
    def test_rejects_unsafe_prediction_label(self):
        for label in ("../secret", "a/b", "a b", ""):
            with self.subTest(label=label), self.assertRaises(ValueError):
                executor.validate_prediction_label(label)

    @patch.object(executor, "METADATA_DIR")
    def test_lists_only_safe_prediction_files(self, metadata_dir):
        metadata_dir.is_dir.return_value = True
        files = []
        for name in ("predictions_from_agent_dev-v2.json", "predictions_from_agent_bad label.json"):
            file = unittest.mock.Mock()
            file.stem = name.removesuffix(".json")
            files.append(file)
        metadata_dir.glob.return_value = files
        self.assertEqual(executor.available_prediction_labels(), ["dev-v2"])


if __name__ == "__main__":
    unittest.main()
