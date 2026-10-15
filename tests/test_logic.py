import unittest
from datetime import datetime, timedelta, timezone

from app.logic import chart_series, is_up_status, summarize, timeout_error


class LogicTests(unittest.TestCase):
    def test_http_success_band(self):
        self.assertTrue(is_up_status(200))
        self.assertTrue(is_up_status(204))
        self.assertTrue(is_up_status(301))
        self.assertTrue(is_up_status(399))
        self.assertFalse(is_up_status(199))
        self.assertFalse(is_up_status(400))
        self.assertFalse(is_up_status(500))
        self.assertFalse(is_up_status(None))

    def test_timeout_message_states_the_limit(self):
        self.assertEqual(timeout_error(30), "No response within 30s")

    def test_summarize_weights_downtime(self):
        start = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        checks = [
            {"checked_at": start.isoformat(), "status": "up", "response_ms": 100},
            {
                "checked_at": (start + timedelta(seconds=60)).isoformat(),
                "status": "down",
                "response_ms": 30000,
                "error": "No response within 30s",
            },
            {
                "checked_at": (start + timedelta(seconds=120)).isoformat(),
                "status": "down",
                "response_ms": 30000,
                "error": "No response within 30s",
            },
            {"checked_at": (start + timedelta(seconds=180)).isoformat(), "status": "up", "response_ms": 80},
        ]
        stats = summarize(checks, now=start + timedelta(seconds=240))
        self.assertEqual(stats["incidents"], 1)
        self.assertEqual(stats["checks"], 4)
        self.assertEqual(stats["down_checks"], 2)
        self.assertEqual(stats["uptime_percent"], 50)
        self.assertEqual(stats["downtime_seconds"], 120)
        self.assertEqual(stats["min_ms"], 80)
        self.assertEqual(stats["max_ms"], 100)

    def test_chart_marks_down_buckets(self):
        start = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        end = start + timedelta(minutes=10)
        checks = [
            {"checked_at": (start + timedelta(minutes=1)).isoformat(), "status": "up", "response_ms": 40},
            {
                "checked_at": (start + timedelta(minutes=2)).isoformat(),
                "status": "down",
                "response_ms": None,
                "error": "Connection failed",
            },
        ]
        series = chart_series(checks, start, end, buckets=10)
        self.assertTrue(any(point["status"] == "down" for point in series))
        self.assertTrue(any(point["response_ms"] == 40 for point in series))


if __name__ == "__main__":
    unittest.main()
