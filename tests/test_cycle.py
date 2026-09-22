from datetime import datetime, timezone
import unittest

from scripts import cycle


class CycleTests(unittest.TestCase):
    def test_requested_window_is_split_below_hosted_job_limit(self):
        now = datetime(2026, 9, 22, 19, tzinfo=timezone.utc)
        plan = cycle.plan(now=now)
        self.assertEqual(plan['deadline'] - now.timestamp(), 24 * 3600 - 30)
        self.assertEqual(plan['segment_seconds'] * 5, 24 * 3600 - 30)
        self.assertLess(plan['segment_seconds'] + 600, 300 * 60)

    def test_short_validation_cycle_and_invalid_durations(self):
        self.assertEqual(cycle.plan(seconds=180)['segment_seconds'], 36)
        for seconds in (0, -1, 86400, 1.5, True):
            with self.assertRaises(ValueError):
                cycle.plan(seconds=seconds)
