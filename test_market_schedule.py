import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import market_schedule as schedule


class MarketScheduleTests(unittest.TestCase):
    def test_monday_is_enabled(self):
        now = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        self.assertTrue(schedule.automatic_jobs_allowed(now))

    def test_friday_is_enabled(self):
        now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
        self.assertTrue(schedule.automatic_jobs_allowed(now))

    def test_saturday_is_disabled(self):
        now = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)
        self.assertFalse(schedule.automatic_jobs_allowed(now))

    def test_sunday_is_disabled(self):
        now = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
        self.assertFalse(schedule.automatic_jobs_allowed(now))

    def test_switch_can_be_disabled(self):
        now = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
        with patch.object(schedule.cfg, "AUTOMATIC_WEEKDAYS_ONLY", False):
            self.assertTrue(schedule.automatic_jobs_allowed(now))


if __name__ == "__main__":
    unittest.main()
