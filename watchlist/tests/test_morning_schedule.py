"""Tests for ``watchlist.morning_schedule``."""

import tempfile
import unittest
from datetime import date
from datetime import datetime
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from watchlist.morning_schedule import POLL_FIRST_LOCAL
from watchlist.morning_schedule import POLL_LAST_LOCAL
from watchlist.morning_schedule import before_polling_window
from watchlist.morning_schedule import in_polling_window
from watchlist.morning_schedule import late_catchup_marker_path
from watchlist.morning_schedule import should_run_scheduled_gate
from watchlist.morning_schedule import try_acquire_late_catchup


class TestMorningSchedule(unittest.TestCase):
    """Poll window and single late catch-up."""

    def setUp(self) -> None:
        self.tz = ZoneInfo('America/Los_Angeles')
        self.desk = date(2026, 5, 27)

    def _at(self, hour: int, minute: int) -> datetime:
        return datetime(2026, 5, 27, hour, minute, tzinfo=self.tz)

    def test_in_polling_window_bounds(self) -> None:
        self.assertTrue(in_polling_window(self._at(5, 58)))
        self.assertTrue(in_polling_window(self._at(6, 10)))
        self.assertFalse(in_polling_window(self._at(5, 57)))
        self.assertFalse(in_polling_window(self._at(6, 11)))

    def test_before_polling_window(self) -> None:
        self.assertTrue(before_polling_window(self._at(5, 0)))
        self.assertFalse(before_polling_window(self._at(6, 0)))

    def test_poll_window_always_runs(self) -> None:
        run, reason = should_run_scheduled_gate(self.desk, now=self._at(6, 2))
        self.assertTrue(run)
        self.assertEqual(reason, 'in_poll_window')

    def test_before_window_skips(self) -> None:
        run, reason = should_run_scheduled_gate(self.desk, now=self._at(5, 30))
        self.assertFalse(run)
        self.assertEqual(reason, 'before_poll_window')

    def test_after_window_runs_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            late = self._at(7, 0)
            run1, r1 = should_run_scheduled_gate(
                self.desk,
                now=late,
                repository_dir=repo,
            )
            run2, r2 = should_run_scheduled_gate(
                self.desk,
                now=late,
                repository_dir=repo,
            )
            self.assertTrue(run1)
            self.assertEqual(r1, 'late_catchup_once')
            self.assertFalse(run2)
            self.assertEqual(r2, 'late_catchup_already_done')
            self.assertTrue(late_catchup_marker_path(self.desk, repo).is_file())

    def test_late_acquire_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.assertTrue(try_acquire_late_catchup(self.desk, repository_dir=repo))
            self.assertFalse(try_acquire_late_catchup(self.desk, repository_dir=repo))

    def test_poll_constants_match_launchd_slots(self) -> None:
        self.assertEqual(POLL_FIRST_LOCAL, time(5, 58))
        self.assertEqual(POLL_LAST_LOCAL, time(6, 10))


if __name__ == '__main__':
    unittest.main()
