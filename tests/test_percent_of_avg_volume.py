"""Integration tests for percent-of-average-volume (2m BarSeries vs prior daily mean).

Requires TWS or IB Gateway with API enabled.

Evaluation **date** and **time** use :mod:`trading.local_time` (default Vancouver).
Converted to naive **ET** for slicing IB bars (``strategies.utils``).

shell cmd
uv run python -m tests.test_percent_of_avg_volume
uv run python -m tests.test_percent_of_avg_volume --date 2026-03-30 --time 09:40
"""

import sys
import unittest
import warnings
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time

from strategies.indicators.percent_of_avg_volume import percent_of_avg_volume
from strategies.utils import RTH_END
from strategies.utils import bar_date
from trading.bar_loader import load_bars
from trading.local_time import local_timezone_name
from trading.local_time import local_wall_to_naive_et
from trading.market_data import connect
from trading.market_data import disconnect
from trading.models import BarSeries

SYMBOL = 'SYY'

# Default scenario (local wall); override with ``--date YYYY-MM-DD`` and ``--time HH:MM``.
DEFAULT_EVAL_DATE_LOCAL = date(2026, 3, 31)
DEFAULT_EVAL_TIME_LOCAL = time(9, 30)


@dataclass(frozen=True)
class _EvalScenarioLocal:
    """Single replay point: local wall clock -> naive ET for bar APIs."""

    date_local: date
    time_local: time
    eval_dt_et: datetime
    load_end_date: date


def _parse_cli_local_scenario() -> _EvalScenarioLocal:
    """Parse ``--date`` / ``--time`` from ``sys.argv`` (local); strip consumed flags."""
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
    """Normalize IB bar .date to naive datetime (strip tzinfo if present)."""
    if isinstance(bar_dt, datetime):
        return bar_dt.replace(tzinfo=None) if bar_dt.tzinfo else bar_dt
    raise TypeError(f'bar .date must be datetime, got {type(bar_dt).__name__}')


def _slice_bars_1d(bars_1d: list, eval_dt: datetime) -> list:
    """Daily bars complete as of eval_dt (same as tests.test_rvol)."""
    result = []
    eval_date = eval_dt.date()
    eval_time = eval_dt.time()
    for b in bars_1d:
        bd = bar_date(b.date)
        if bd is None:
            continue
        if bd < eval_date or bd == eval_date and eval_time >= RTH_END:
            result.append(b)
    return result


def _slice_bars_2min(bars_2min: list, eval_dt: datetime) -> list:
    """2m bars through eval_dt."""
    out = []
    for b in bars_2min:
        dt = _bar_timestamp_as_naive_datetime(b.date)
        if dt <= eval_dt:
            out.append(b)
    return out


class TestPercentOfAvgVolumeIntegration(unittest.TestCase):
    """Integration tests against IB historical data."""

    ib = None
    bundle = None

    @classmethod
    def setUpClass(cls) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=DeprecationWarning)
            cls.ib = connect(readonly=True)
        assert cls.ib is not None and cls.ib.isConnected()
        cls.bundle = load_bars(cls.ib, SYMBOL, end_date=SCENARIO_LOCAL.load_end_date)

    @classmethod
    def tearDownClass(cls) -> None:
        disconnect(cls.ib)

    def test_percent_of_avg_volume(self) -> None:
        bundle = self.bundle
        if bundle is None or not bundle.bars_1d or not bundle.bars_2min:
            self.skipTest('load_bars returned incomplete bundle')
        assert bundle is not None

        eval_dt = SCENARIO_LOCAL.eval_dt_et
        bars_2min = bundle.bars_2min
        sliced_1d = _slice_bars_1d(bundle.bars_1d, eval_dt)
        sliced_2min = _slice_bars_2min(bars_2min, eval_dt)
        if not sliced_1d:
            self.skipTest('No daily bars complete as of evaluation time')

        series = BarSeries(bars_1d=sliced_1d, bars_2min=sliced_2min)
        result = percent_of_avg_volume(series, eval_as_of=eval_dt)
        tz_label = local_timezone_name()
        session_label = (
            f'{SCENARIO_LOCAL.date_local.isoformat()} '
            f'{SCENARIO_LOCAL.time_local.strftime("%H:%M")} {tz_label} '
            f'-> {eval_dt.isoformat(sep=" ", timespec="minutes")} ET'
        )
        if result is None:
            print(
                f'{SYMBOL} | {session_label} | '
                f'percent_of_avg_volume = False\n'
                f'result = None\n'
            )
            self.skipTest('percent_of_avg_volume returned None')

        assert result is not None
        pct = result.percent_of_average
        pct_line = f'{pct:.1f}' if pct is not None else 'na'
        print(
            f'{SYMBOL} | {session_label} | '
            f'percent_of_avg_volume = True\n'
            f'average_volume = {result.average_volume:.0f}\n'
            f'percent_of_average = {pct_line}\n'
            f'above_threshold ({result.threshold_pct}%) | {result.above_threshold}\n'
            f'threshold_locked | {result.threshold_locked}\n'
        )
        self.assertIsNotNone(result.average_volume)
        if result.active_volume is not None:
            self.assertGreater(result.active_volume, 0.0)


if __name__ == '__main__':
    unittest.main(buffer=False)
