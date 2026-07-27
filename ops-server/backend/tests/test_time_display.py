import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
sys.path.insert(0, str(FRONTEND_DIR))

from components.time_display import (  # noqa: E402
    format_kst,
    format_kst_columns,
    to_kst_naive_series,
)


class TimeDisplayTest(unittest.TestCase):
    def test_utc_timestamp_is_displayed_in_kst(self):
        self.assertEqual(
            format_kst("2026-07-22T02:16:00.476621+00:00"),
            "2026-07-22 11:16:00 KST",
        )
        self.assertEqual(
            format_kst("2026-07-22T02:16:00Z"),
            "2026-07-22 11:16:00 KST",
        )

    def test_naive_datetime_is_treated_as_utc(self):
        self.assertEqual(
            format_kst(datetime(2026, 7, 22, 2, 16)),
            "2026-07-22 11:16:00 KST",
        )
        self.assertEqual(
            format_kst(datetime(2026, 7, 22, 2, 16, tzinfo=timezone.utc)),
            "2026-07-22 11:16:00 KST",
        )

    def test_invalid_timestamp_remains_visible(self):
        self.assertEqual(format_kst(None), "-")
        self.assertEqual(format_kst(pd.NaT), "-")
        self.assertEqual(format_kst("not-a-timestamp"), "not-a-timestamp")

    def test_chart_series_is_kst_and_timezone_naive(self):
        result = to_kst_naive_series(pd.Series(["2026-07-22T02:16:00Z"]))

        self.assertEqual(result.iloc[0], pd.Timestamp("2026-07-22 11:16:00"))
        self.assertIsNone(result.dt.tz)

    def test_dataframe_columns_are_formatted_without_mutating_source(self):
        source = pd.DataFrame({"created_at": ["2026-07-22T02:16:00Z"]})
        result = format_kst_columns(source, ["created_at"])

        self.assertEqual(result.iloc[0]["created_at"], "2026-07-22 11:16:00 KST")
        self.assertEqual(source.iloc[0]["created_at"], "2026-07-22T02:16:00Z")


if __name__ == "__main__":
    unittest.main()
