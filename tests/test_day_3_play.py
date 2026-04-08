"""Integration tests for day_3_play bar pattern.

Requires TWS or IB Gateway running with API enabled. Sends SYMBOL to IB, loads bars via load_bars,
runs day_3_play_stats and day_3_play with that data. Optional --date YYYY-MM-DD loads bars
through that date (run module as main: uv run python -m tests.test_day_3_play --date 2025-03-12).
shell cmd
uv run python -m tests.test_day_3_play
uv run python -m tests.test_day_3_play --date 2025-03-12
"""

import sys
import unittest
import warnings
from datetime import date

from strategies.bar_patterns.day_3_play import MIN_DAILY_BARS
from strategies.bar_patterns.day_3_play import day_3_play
from strategies.bar_patterns.day_3_play import day_3_play_stats
from trading.bar_loader import load_bars
from trading.market_data import connect
from trading.market_data import disconnect

SYMBOL = 'NBIS'


def _parse_and_strip_date() -> date | None:
    """Parse --date YYYY-MM-DD from sys.argv, remove from argv, return date or None."""
    argv = sys.argv[1:]
    result: date | None = None
    new_argv: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--date' and i + 1 < len(argv):
            try:
                result = date.fromisoformat(argv[i + 1])
                i += 2
                continue
            except (ValueError, TypeError):
                pass
        if a.startswith('--date='):
            try:
                result = date.fromisoformat(a.split('=', 1)[1])
                i += 1
                continue
            except (ValueError, TypeError):
                pass
        new_argv.append(a)
        i += 1
    sys.argv = [sys.argv[0]] + new_argv
    return result


_END_DATE: date | None = _parse_and_strip_date()


class TestDay3PlayIntegration(unittest.TestCase):
    """Integration tests: load bars from IB for SYMBOL, run day_3_play_stats / day_3_play."""

    ib = None
    bundle = None

    @classmethod
    def setUpClass(cls) -> None:
        """Connect once and load bars once for all tests."""
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=DeprecationWarning)
            cls.ib = connect(readonly=True)
        assert cls.ib is not None and cls.ib.isConnected()
        cls.bundle = load_bars(cls.ib, SYMBOL, end_date=_END_DATE)

    @classmethod
    def tearDownClass(cls) -> None:
        """Disconnect after all tests."""
        disconnect(cls.ib)

    def test_day_3_play_stats_with_ib(self) -> None:
        """Load bars from IB for SYMBOL, run day_3_play_stats and day_3_play; assert types and consistency."""
        assert self.ib is not None
        bundle = self.bundle
        if bundle is None:
            self.skipTest(f'No bar bundle for {SYMBOL}')
        assert bundle is not None
        if len(bundle.bars_1d) < MIN_DAILY_BARS:
            self.skipTest(
                f'Need at least {MIN_DAILY_BARS} daily bars for {SYMBOL}'
            )

        stats = day_3_play_stats(bundle, ib=self.ib, symbol=SYMBOL)

        self.assertIsInstance(stats.triggered, bool)
        self.assertIsInstance(stats.day_1_qualified, bool)
        self.assertIsInstance(stats.day_2_consolidated, bool)
        if stats.day_2_high is not None:
            self.assertIsInstance(stats.day_2_high, float)
        if stats.day_3_high is not None:
            self.assertIsInstance(stats.day_3_high, float)
        if stats.rvol is not None:
            self.assertIsInstance(stats.rvol, float)
        if stats.adr is not None:
            self.assertIsInstance(stats.adr, float)

        triggered = day_3_play(bundle, ib=self.ib, symbol=SYMBOL)
        self.assertIsInstance(triggered, bool)
        self.assertEqual(triggered, stats.triggered)

        day_3_above = (
            stats.day_3_high is not None
            and stats.day_2_high is not None
            and stats.day_3_high > stats.day_2_high
        )
        passed = []
        failed = []
        if stats.day_1_up_only:
            passed.append('day_1_up_only')
        else:
            failed.append('day_1_up_only')
        if stats.day_1_rvol_ok:
            passed.append('day_1_rvol_ok')
        else:
            failed.append('day_1_rvol_ok')
        if stats.day_1_range_ok:
            passed.append('day_1_range_ok')
        else:
            failed.append('day_1_range_ok')
        if stats.day_2_consolidated:
            passed.append('day_2_consolidated')
        else:
            failed.append('day_2_consolidated')
        if day_3_above:
            passed.append('day_3_above_high')
        else:
            failed.append('day_3_above_high')

        print(f'{SYMBOL} triggered={stats.triggered} passed: {passed} failed: {failed}')


if __name__ == '__main__':
    unittest.main(buffer=False)
