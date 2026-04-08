"""Integration test for gap indicator using IB historical bars.

Requires TWS or IB Gateway with API enabled.

Evaluation **date** and **time** are in :mod:`trading.local_time` (default Vancouver).
Converted to naive **ET** for ``gap`` and IB bar slicing (``strategies.utils``).

shell cmd
uv run python -m tests.test_gap
uv run python -m tests.test_gap --date 2026-04-08 --time 06:30
"""

import sys
import unittest
import warnings
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time

from strategies.indicators.gap import gap
from strategies.utils import RTH_END
from strategies.utils import bar_date
from trading.bar_loader import load_bars
from trading.local_time import local_timezone_name
from trading.local_time import local_wall_to_naive_et
from trading.market_data import connect
from trading.market_data import disconnect
from trading.models import BarSeries

SYMBOL = 'USO'

DEFAULT_EVAL_DATE_LOCAL = date(2026, 4, 8)
DEFAULT_EVAL_TIME_LOCAL = time(6, 30)


@dataclass(frozen=True)
class _EvalScenarioLocal:
    date_local: date
    time_local: time
    eval_dt_et: datetime
    load_end_date: date


def _parse_cli_local_scenario() -> _EvalScenarioLocal:
    d_out = DEFAULT_EVAL_DATE_LOCAL
    t_out = DEFAULT_EVAL_TIME_LOCAL
    argv = sys.argv[1:]
    new_argv: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--date' and i + 1 < len(argv):
            d_out = date.fromisoformat(argv[i + 1])
            i += 2
            continue
        if a.startswith('--date='):
            d_out = date.fromisoformat(a.split('=', 1)[1])
            i += 1
            continue
        if a == '--time' and i + 1 < len(argv):
            h, m = map(int, argv[i + 1].split(':'))
            t_out = time(h, m)
            i += 2
            continue
        if a.startswith('--time='):
            part = a.split('=', 1)[1]
            h, m = map(int, part.split(':'))
            t_out = time(h, m)
            i += 1
            continue
        new_argv.append(a)
        i += 1
    sys.argv = [sys.argv[0]] + new_argv

    eval_et = local_wall_to_naive_et(d_out, t_out)
    return _EvalScenarioLocal(
        date_local=d_out,
        time_local=t_out,
        eval_dt_et=eval_et,
        load_end_date=eval_et.date(),
    )


SCENARIO_LOCAL = _parse_cli_local_scenario()


def _bar_timestamp_as_naive_datetime(bar_dt: object) -> datetime:
    """Normalize IB bar timestamp to naive datetime."""
    if isinstance(bar_dt, datetime):
        return bar_dt.replace(tzinfo=None) if bar_dt.tzinfo else bar_dt
    raise TypeError(f'bar .date must be datetime, got {type(bar_dt).__name__}')


def _slice_bars_1d(bars_1d: list, eval_dt: datetime) -> list:
    """Daily bars complete as of eval_dt."""
    result = []
    eval_date = eval_dt.date()
    eval_time = eval_dt.time()
    for bar in bars_1d:
        bar_day = bar_date(bar.date)
        if bar_day is None:
            continue
        if bar_day < eval_date or bar_day == eval_date and eval_time >= RTH_END:
            result.append(bar)
    return result


def _slice_bars_2min(bars_2min: list, eval_dt: datetime) -> list:
    """2-minute bars through eval_dt."""
    result = []
    for bar in bars_2min:
        bar_dt = _bar_timestamp_as_naive_datetime(bar.date)
        if bar_dt <= eval_dt:
            result.append(bar)
    return result


class TestGapIntegration(unittest.TestCase):
    """Integration test for gap against IB data."""

    @classmethod
    def setUpClass(cls) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=DeprecationWarning)
            cls.ib = connect(readonly=True)
        assert cls.ib is not None
        assert cls.ib.isConnected()
        cls.bundle = load_bars(cls.ib, SYMBOL, end_date=SCENARIO_LOCAL.load_end_date)

    @classmethod
    def tearDownClass(cls) -> None:
        disconnect(cls.ib)

    def test_gap_value(self) -> None:
        bundle = self.bundle
        if bundle is None:
            self.skipTest('load_bars returned no bundle')
        assert bundle is not None
        eval_dt = SCENARIO_LOCAL.eval_dt_et
        sliced_1d = _slice_bars_1d(bundle.bars_1d, eval_dt)
        sliced_2min = _slice_bars_2min(bundle.bars_2min, eval_dt)
        series = BarSeries(bars_1d=sliced_1d, bars_2min=sliced_2min)
        gap_value = gap(series, eval_as_of=eval_dt)
        self.assertIsNotNone(gap_value)
        assert gap_value is not None
        self.assertIsNotNone(gap_value.gap_atr)
        tz_label = local_timezone_name()
        session_label = (
            f'{SCENARIO_LOCAL.date_local.isoformat()} '
            f'{SCENARIO_LOCAL.time_local.strftime("%H:%M")} {tz_label} '
            f'-> {eval_dt.isoformat(sep=" ", timespec="minutes")} ET'
        )
        print(f'{SYMBOL} | {session_label} | gap = True')
        print(f'gap_percent = {gap_value.gap_percent:.4f} | True')
        print(f'gap_atr = {gap_value.gap_atr:.4f} | True')
        print(f'prior_close = {gap_value.prior_close:.4f} | True')
        print(f'reference_price = {gap_value.reference_price:.4f} | True')


if __name__ == '__main__':
    unittest.main(buffer=False)
