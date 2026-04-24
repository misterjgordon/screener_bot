"""Integration tests for gap pattern scans (day_3_gap on daily bars).

Requires TWS or IB Gateway running with API enabled. Loads bars via load_bars.

Optional ``--date YYYY-MM-DD`` loads daily bars through that calendar day (ET window
from bar_loader). Run as main:

shell cmd
uv run python -m tests.test_gaps
uv run python -m tests.test_gaps --date 2026-04-08
"""

import sys
import unittest
import warnings
from datetime import date
from datetime import datetime
from typing import TYPE_CHECKING

from strategies.bar_patterns.gaps import _is_gap_up
from strategies.bar_patterns.gaps import day_3_gap
from trading.bar_loader import load_bars
from trading.market_data import connect
from trading.market_data import disconnect

if TYPE_CHECKING:
    from trading.models import BarSeries

SYMBOL = 'SMR'


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


def _bar_date(bar_date_value: object) -> date | None:
    """Normalize bar date value to date."""
    if isinstance(bar_date_value, datetime):
        return bar_date_value.date()
    if isinstance(bar_date_value, date):
        return bar_date_value
    return None


def _day_3_gap_factor_lines(bundle: 'BarSeries') -> list[str]:
    """Print context: value-only lines, then test lines ``name = value | passed``."""
    bars_daily = bundle.bars_1d
    n = len(bars_daily)
    lines: list[str] = [f'bars_1d_count = {n}']
    if n < 3:
        return lines

    day_1, day_2, day_3 = bars_daily[-3:]
    day_1_vol = day_1.volume
    vol_ok = day_1_vol > 0
    lines.append(f'day_1_volume = {day_1_vol}')

    gap_2 = _is_gap_up(day_1, day_2)
    lines.append(f'day_2_gap_up = {gap_2} | {gap_2}')

    gap_3 = _is_gap_up(day_2, day_3)
    lines.append(f'day_3_gap_up = {gap_3} | {gap_3}')

    if not vol_ok:
        ratio = 0.0
        ratio_ok = False
    else:
        ratio = day_2.volume / day_1_vol
        ratio_ok = ratio >= 1.2
    lines.append(f'day_2_volume_ratio = {ratio:.4f} | {ratio_ok}')

    return lines


class TestGapsIntegration(unittest.TestCase):
    """Integration tests against real IB historical data."""

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

    def test_day_3_gap_result(self) -> None:
        """Fetch daily bars via bar_loader, run day_3_gap, print factor breakdown."""
        bundle = self.bundle

        if bundle is None or not bundle.bars_1d:
            self.skipTest('No daily bars returned')
        assert bundle is not None

        result = day_3_gap(bundle)
        self.assertIsInstance(result, bool)

        latest_day = _bar_date(bundle.bars_1d[-1].date)
        session_date = latest_day.isoformat() if latest_day else 'n/a'

        print(f'{SYMBOL} | {session_date} | day_3_gap = {result}')
        for line in _day_3_gap_factor_lines(bundle):
            print(line)


if __name__ == '__main__':
    unittest.main(buffer=False)
