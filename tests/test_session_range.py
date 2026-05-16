"""Integration tests for desk :mod:`strategies.indicators.session_range` (PT windows, ET bars).

Uses IB ``load_bars`` with :data:`~strategies.indicators.session_range.BARS_2MIN_DURATION_FOR_DESK_SESSION_RANGES`
for 2m history so prior-day after-hours is available; skips when disconnected or data is incomplete.

shell cmd
uv run python -m tests.test_session_range
uv run python -m tests.test_session_range --date 2026-04-15 --time 12:00
"""

import sys
import unittest
import warnings
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time

from strategies.indicators.adr import calculate_adr
from strategies.indicators.session_range import BARS_2MIN_DURATION_FOR_DESK_SESSION_RANGES
from strategies.indicators.session_range import DeskSessionRanges
from strategies.indicators.session_range import SessionOhlcAdr
from strategies.indicators.session_range import compute_desk_session_ranges
from strategies.utils import RTH_END
from strategies.utils import bar_date
from trading.bar_loader import load_bars
from trading.local_time import local_timezone_name
from trading.local_time import local_wall_to_naive_et
from trading.market_data import connect
from trading.market_data import disconnect
from trading.models import BarSeries

SYMBOL = 'MYSE'

# Default: local wall near cash open; override with ``--date`` / ``--time``.
DEFAULT_EVAL_DATE_LOCAL = date(2026, 4, 16)
DEFAULT_EVAL_TIME_LOCAL = time(12, 0)

# Arbitrary session_date for ``test_empty_bars_returns_none`` (no bars to slice).
EMPTY_BARS_SESSION_DATE = date(2026, 1, 5)


@dataclass(frozen=True)
class _EvalScenarioLocal:
    """Local wall clock -> naive ET for bar APIs."""

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
    if isinstance(bar_dt, datetime):
        return bar_dt.replace(tzinfo=None) if bar_dt.tzinfo else bar_dt
    raise TypeError(f'bar .date must be datetime, got {type(bar_dt).__name__}')


def _slice_bars_1d(bars_1d: list, eval_dt: datetime) -> list:
    result: list = []
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
    out: list = []
    for b in bars_2min:
        dt = _bar_timestamp_as_naive_datetime(b.date)
        if dt <= eval_dt:
            out.append(b)
    return out


def _session_consistent(s: SessionOhlcAdr) -> bool:
    if s.open is None or s.high is None or s.low is None or s.close is None:
        return True
    return s.high >= s.low


class TestSessionRangeIntegration(unittest.TestCase):
    """Desk session OHLC + ADR vs IB history."""

    ib = None
    bundle = None

    @classmethod
    def setUpClass(cls) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=DeprecationWarning)
            cls.ib = connect(readonly=True)
        if cls.ib is None or not cls.ib.isConnected():
            return
        cls.bundle = load_bars(
            cls.ib,
            SYMBOL,
            end_date=SCENARIO_LOCAL.load_end_date,
            duration_str_2min=BARS_2MIN_DURATION_FOR_DESK_SESSION_RANGES,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        disconnect(cls.ib)

    def setUp(self) -> None:
        if self.ib is None or not self.ib.isConnected():
            self.skipTest('IB not connected')

    def test_empty_bars_returns_none(self) -> None:
        self.assertIsNone(
            compute_desk_session_ranges([], session_date=EMPTY_BARS_SESSION_DATE),
        )

    def test_desk_session_ranges(self) -> None:
        bundle = self.bundle
        if bundle is None or not bundle.bars_1d or not bundle.bars_2min:
            self.skipTest('load_bars incomplete')

        assert bundle is not None
        bars_1d = bundle.bars_1d

        eval_dt = SCENARIO_LOCAL.eval_dt_et
        sliced_1d = _slice_bars_1d(bars_1d, eval_dt)
        sliced_2m = _slice_bars_2min(bundle.bars_2min, eval_dt)
        if not sliced_1d:
            self.skipTest('No daily bars complete as of evaluation time')

        session_date = eval_dt.date()
        ranges = compute_desk_session_ranges(
            sliced_2m,
            session_date=session_date,
            bars_1d=sliced_1d,
            eval_as_of=eval_dt,
        )
        adr_row = calculate_adr(
            None,
            '',
            bundle=BarSeries(bars_1d=sliced_1d, bars_2min=[]),
        )
        tz_label = local_timezone_name()
        session_label = (
            f'{SCENARIO_LOCAL.date_local.isoformat()} '
            f'{SCENARIO_LOCAL.time_local.strftime("%H:%M")} {tz_label} '
            f'-> {eval_dt.isoformat(sep=" ", timespec="minutes")} ET'
        )

        if ranges is None:
            print(
                f'{SYMBOL} | {session_date} | session_range = False\n'
                f'ranges = None\n'
                f'{session_label}\n',
            )
            self.skipTest('compute_desk_session_ranges returned None')

        assert ranges is not None
        ranges_n: DeskSessionRanges = ranges

        def _fmt(s: SessionOhlcAdr) -> str:
            if s.open is None:
                return 'no bars'
            ratio_s = f'{s.adr_change_percent:.2f}' if s.adr_change_percent is not None else 'na'
            return (
                f'o={s.open:.2f} h={s.high:.2f} l={s.low:.2f} c={s.close:.2f} '
                f'change={s.change:.4f} adr_ch={ratio_s}'
            )

        prior_has = ranges_n.prior_day_ah_session.open is not None
        pm_has = ranges_n.pm_session.open is not None
        or_has = ranges_n.opening_range_session.open is not None

        print(
            f'{SYMBOL} | {session_date} | session_range = True\n'
            f'adr_20 = {adr_row}\n'
            f'{session_label}\n'
            f'prior_day_ah_session | {_fmt(ranges_n.prior_day_ah_session)}\n'
            f'pm_session | {_fmt(ranges_n.pm_session)}\n'
            f'opening_range_session | {_fmt(ranges_n.opening_range_session)}\n'
            f'morning_session | {_fmt(ranges_n.morning_session)}\n'
            f'afternoon_session | {_fmt(ranges_n.afternoon_session)}\n'
            f'closing_session | {_fmt(ranges_n.closing_session)}\n'
            f'prior_day_ah_has_bars = {prior_has}\n'
            f'pm_has_bars = {pm_has}\n'
            f'opening_range_has_bars = {or_has}\n'
            f'extended_session_coverage = {prior_has or pm_has} | True\n',
        )

        for name, seg in (
            ('prior_day_ah_session', ranges_n.prior_day_ah_session),
            ('pm_session', ranges_n.pm_session),
            ('opening_range_session', ranges_n.opening_range_session),
            ('morning_session', ranges_n.morning_session),
            ('afternoon_session', ranges_n.afternoon_session),
            ('closing_session', ranges_n.closing_session),
        ):
            self.assertTrue(
                _session_consistent(seg),
                msg=f'{name} OHLC inconsistent',
            )
            if (
                seg.change is not None
                and seg.adr_change_percent is not None
                and seg.open is not None
                and seg.close is not None
                and seg.high is not None
                and seg.low is not None
                and adr_row
                and adr_row > 0
            ):
                raw_change = float(seg.high) - float(seg.low)
                self.assertAlmostEqual(
                    seg.change,
                    round(raw_change, 2),
                    places=5,
                    msg=f'{name} change',
                )
                raw_net = float(seg.close) - float(seg.open)
                signed_range = raw_change if raw_net >= 0 else -raw_change
                expected_ratio = round(signed_range / adr_row, 2)
                self.assertAlmostEqual(
                    seg.adr_change_percent,
                    expected_ratio,
                    places=5,
                    msg=f'{name} adr_change_percent',
                )

        self.assertTrue(prior_has or pm_has, msg='expected some extended-hours coverage')


if __name__ == '__main__':
    unittest.main(buffer=False)
