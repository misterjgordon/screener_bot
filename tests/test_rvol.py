"""Integration tests for Relative Volume indicator.

Requires TWS or IB Gateway running with API enabled. Use --time HH:MM to test
at a specific time of day (e.g. --time 10:30); if None, uses current time.
If current time is not during RTH (9:30-16:00 ET), defaults to last RTH bar.
shell cmd
uv run python -m tests.test_rvol
uv run python -m tests.test_rvol --time 10:30
"""

import sys
import unittest
import warnings
from datetime import datetime
from datetime import time

from strategies.indicators.rvol import rvol
from strategies.utils import RTH_END
from strategies.utils import RTH_START
from strategies.utils import bar_date
from strategies.utils import last_trading_day
from trading.bar_loader import load_bars
from trading.market_data import connect
from trading.market_data import disconnect
from trading.models import BarSeries

SYMBOL = 'HIMS'


def _eval_time(test_time: time | None, bars_2min: list, bars_2min_rth: list) -> datetime:
    """Compute evaluation datetime: test_time, now(), or last RTH bar if outside hours."""
    session_date = last_trading_day()
    if test_time is not None:
        return datetime.combine(session_date, test_time)
    now = datetime.now()
    if RTH_START <= now.time() < RTH_END:
        return now
    if bars_2min_rth:
        bar_dt = bars_2min_rth[-1].date
        return bar_dt.replace(tzinfo=None) if hasattr(bar_dt, 'tzinfo') and bar_dt.tzinfo else bar_dt
    if bars_2min:
        bar_dt = bars_2min[-1].date
        return bar_dt.replace(tzinfo=None) if hasattr(bar_dt, 'tzinfo') and bar_dt.tzinfo else bar_dt
    return now


def _slice_bars_1d(bars_1d: list, eval_dt: datetime) -> list:
    """Return daily bars complete as of eval_dt. Bar for date D is complete after 4 PM ET on D."""
    result = []
    eval_date = eval_dt.date()
    eval_time = eval_dt.time()
    for b in bars_1d:
        bd = bar_date(b.date) if hasattr(b, 'date') else None
        if bd is None:
            continue
        if bd < eval_date or bd == eval_date and eval_time >= RTH_END:
            result.append(b)
    return result


def _parse_and_strip_time() -> time | None:
    """Parse --time HH:MM from sys.argv, remove from argv, return time or None."""
    argv = sys.argv[1:]
    result: time | None = None
    new_argv: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--time' and i + 1 < len(argv):
            try:
                h, m = map(int, argv[i + 1].split(':'))
                result = time(h, m)
                i += 2
                continue
            except (ValueError, IndexError):
                pass
        if a.startswith('--time='):
            try:
                part = a.split('=', 1)[1]
                h, m = map(int, part.split(':'))
                result = time(h, m)
                i += 1
                continue
            except (ValueError, IndexError):
                pass
        new_argv.append(a)
        i += 1
    sys.argv = [sys.argv[0]] + new_argv
    return result


_TEST_TIME: time | None = _parse_and_strip_time()


class TestRvolIntegration(unittest.TestCase):
    """Integration tests for rvol against IB historical data."""

    ib = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.ib = connect(readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        disconnect(cls.ib)

    def setUp(self) -> None:
        if self.ib is None or not self.ib.isConnected():
            self.skipTest('IB not connected - is TWS/Gateway running?')

    def test_rvol_value(self) -> None:
        """Load bars for SYMBOL, slice to eval time, compute rvol (uses bars_1d)."""
        test_time = _TEST_TIME
        bundle = load_bars(self.ib, SYMBOL)
        if bundle is None or not bundle.bars_1d:
            self.skipTest('No daily bars returned')
        assert bundle is not None

        bars_2min = getattr(bundle, 'bars_2min', []) or []
        bars_2min_rth = getattr(bundle, 'bars_2min_rth', []) or []
        eval_dt = _eval_time(test_time, bars_2min, bars_2min_rth)
        sliced_1d = _slice_bars_1d(bundle.bars_1d, eval_dt)
        if not sliced_1d:
            self.skipTest('No daily bars complete as of evaluation time')
        sliced_series = BarSeries(bars_1d=sliced_1d, bars_2min=[])

        rvol_val = rvol(sliced_series, period=10)
        last_bar_date = bar_date(sliced_1d[-1].date) if sliced_1d else None
        print(
            f'{SYMBOL} rvol={rvol_val} | eval_dt={eval_dt} | last_complete_day={last_bar_date} | '
            f'bars_1d={len(sliced_1d)}'
        )
        if rvol_val is not None:
            self.assertIsInstance(rvol_val, float)


if __name__ == '__main__':
    warnings.filterwarnings('ignore', category=DeprecationWarning, module='ib_async')
    unittest.main(buffer=False)
