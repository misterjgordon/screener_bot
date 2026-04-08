"""Integration tests for trend exhaustion reversal pattern.

Requires TWS or IB Gateway running with API enabled.

``TEST_LOOKBACK_END_TIME`` is **local wall** (:mod:`trading.local_time`, default Vancouver).
It is converted to the same convention as ``Bar.date`` (naive US/Eastern from IB) before
truncating 2-min bars.

shell cmd
uv run --frozen python -m tests.test_trend_exhaustion_reversal
uv run --frozen python -m tests.test_trend_exhaustion_reversal --date 2026-03-31 --time 11:45
``--time`` is **local**, same as ``TEST_LOOKBACK_END_TIME``.
"""

import sys
import unittest
import warnings
from datetime import date
from datetime import datetime
from datetime import time

from strategies.bar_patterns.trend_exhaustion_reversal import ATR_PERIOD
from strategies.bar_patterns.trend_exhaustion_reversal import COMPRESSION_BARS
from strategies.bar_patterns.trend_exhaustion_reversal import MAX_BARS_SINCE_LAST_TREND_CHANGE
from strategies.bar_patterns.trend_exhaustion_reversal import MIN_TRAVEL_FROM_LAST_TREND_CHANGE_ATR
from strategies.bar_patterns.trend_exhaustion_reversal import REVERSAL_BAR_LOOKBACK_BARS
from strategies.bar_patterns.trend_exhaustion_reversal import REVERSAL_BAR_MIN_PRIOR_RANGE_ATR
from strategies.bar_patterns.trend_exhaustion_reversal import REVERSAL_BAR_MIN_TRIGGER_RANGE_FRAC
from strategies.bar_patterns.trend_exhaustion_reversal import TRIGGER_CLOSE_LOCATION_MIN
from strategies.bar_patterns.trend_exhaustion_reversal import VOLUME_MA_PERIOD
from strategies.bar_patterns.trend_exhaustion_reversal import VOLUME_PERCENTILE_MAX
from strategies.bar_patterns.trend_exhaustion_reversal import TrendExhaustionContextSnapshot
from strategies.bar_patterns.trend_exhaustion_reversal import TrendExhaustionSetup
from strategies.bar_patterns.trend_exhaustion_reversal import _first_ema9_upturn_idx_since
from strategies.bar_patterns.trend_exhaustion_reversal import _last_trend_change
from strategies.bar_patterns.trend_exhaustion_reversal import trend_exhaustion_context_snapshot
from strategies.bar_patterns.trend_exhaustion_reversal import trend_exhaustion_most_recent_volume_compression_end_index
from strategies.bar_patterns.trend_exhaustion_reversal import trend_exhaustion_probe_last_bar
from strategies.bar_patterns.trend_exhaustion_reversal import trend_exhaustion_reversal
from strategies.bar_patterns.trend_exhaustion_reversal import trend_exhaustion_reversal_bar_probe_last
from strategies.bar_patterns.trend_exhaustion_reversal import trend_exhaustion_reversal_scan
from strategies.indicators.atr import atr
from trading.bar_loader import load_bars
from trading.local_time import local_zone
from trading.local_time import session_et_zone
from trading.market_data import connect
from trading.market_data import disconnect
from trading.models import BarSeries

SYMBOL = 'USO'

# Defaults when ``--date`` / ``--time`` are omitted (local wall for lookback end).
DEFAULT_TEST_SESSION_DATE: date | None = date(2026, 3, 31)
DEFAULT_TEST_LOOKBACK_END_TIME: time | None = time(10, 50)


def _parse_cli_test_options() -> tuple[date | None, time | None]:
    """Parse ``--date`` / ``--time HH:MM`` (local); strip flags so unittest sees argv."""
    argv = sys.argv[1:]
    new_argv: list[str] = []
    parsed_date: date | None = None
    parsed_time: time | None = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--date' and i + 1 < len(argv):
            parsed_date = date.fromisoformat(argv[i + 1])
            i += 2
            continue
        if a.startswith('--date='):
            parsed_date = date.fromisoformat(a.split('=', 1)[1])
            i += 1
            continue
        if a == '--time' and i + 1 < len(argv):
            h, m = map(int, argv[i + 1].split(':'))
            parsed_time = time(h, m)
            i += 2
            continue
        if a.startswith('--time='):
            part = a.split('=', 1)[1]
            h, m = map(int, part.split(':'))
            parsed_time = time(h, m)
            i += 1
            continue
        new_argv.append(a)
        i += 1
    sys.argv = [sys.argv[0], *new_argv]
    return (parsed_date, parsed_time)


_CLI_SESSION_DATE, _CLI_LOOKBACK_END_TIME = _parse_cli_test_options()
TEST_SESSION_DATE: date | None = (
    _CLI_SESSION_DATE if _CLI_SESSION_DATE is not None else DEFAULT_TEST_SESSION_DATE
)
TEST_LOOKBACK_END_TIME: time | None = (
    _CLI_LOOKBACK_END_TIME
    if _CLI_LOOKBACK_END_TIME is not None
    else DEFAULT_TEST_LOOKBACK_END_TIME
)


def _bar_datetime_naive_et(bar_dt: datetime) -> datetime:
    """Normalize IB bar time to naive US/Eastern for comparisons with cutoff datetimes."""
    if bar_dt.tzinfo is None:
        return bar_dt
    return bar_dt.astimezone(session_et_zone()).replace(tzinfo=None)


def _cutoff_naive_et_from_session_end_local(session_day: date, end_time_local: time) -> datetime:
    """Convert session calendar day + local end-of-window clock to naive Eastern (IB bar times)."""
    aware_local = datetime.combine(session_day, end_time_local, tzinfo=local_zone())
    return aware_local.astimezone(session_et_zone()).replace(tzinfo=None)


def _bar_series_2min_through_end_time(
    bar_series: BarSeries,
    end_time: time | None,
    *,
    session_date: date,
) -> BarSeries:
    """Truncate 2-min bars to those at or before ``end_time`` (local on ``session_date``)."""
    if end_time is None:
        return bar_series
    cutoff = _cutoff_naive_et_from_session_end_local(session_date, end_time)
    bars = [b for b in bar_series.bars_2min if _bar_datetime_naive_et(b.date) <= cutoff]
    return BarSeries(bars_1d=bar_series.bars_1d, bars_2min=bars)


def _print_full_scan_gates_last_bar(bar_series: BarSeries) -> None:
    """Print EMA9 first-upturn index vs context (informational; not a scan gate)."""
    bars = bar_series.bars_2min
    if len(bars) < 2:
        return
    context_idx = len(bars) - 2
    daily_atr = atr(bar_series.bars_1d, period=ATR_PERIOD)
    if daily_atr is None or daily_atr <= 0:
        print('full_scan: ema9_first_upturn_at_context=n/a (daily_atr)')
        return
    tc = _last_trend_change(bar_series, bars, context_idx)
    if tc is None:
        print('full_scan: ema9_first_upturn_at_context_idx | n/a (no trend_change)')
        return
    tc_idx = tc[0]
    fu = _first_ema9_upturn_idx_since(bar_series, bars, tc_idx, context_idx)
    upturn_ok = fu is not None and fu == context_idx
    print(
        f'full_scan: ema9_first_upturn_idx={fu} context_bar_idx={context_idx} '
        f'(informational) | {upturn_ok}'
    )


def _compression_last_bar_clock_local(
    bar_series: BarSeries,
    setup: TrendExhaustionSetup | None,
) -> str:
    """Last bar of the compression block: setup trigger minus one, else most recent volume pass (scan logic)."""
    bars_2 = bar_series.bars_2min
    if setup is not None and setup.trigger_bar_index >= 1:
        dt = bars_2[setup.trigger_bar_index - 1].date
    else:
        end_idx = trend_exhaustion_most_recent_volume_compression_end_index(bar_series)
        if end_idx is None:
            return 'n/a'
        dt = bars_2[end_idx].date
    naive_et = _bar_datetime_naive_et(dt)
    return naive_et.replace(tzinfo=session_et_zone()).astimezone(local_zone()).strftime('%H:%M:%S %Z')


def _print_scan_report(
    symbol: str,
    session_date: date,
    triggered: bool,
    setup: TrendExhaustionSetup | None,
    snapshot: TrendExhaustionContextSnapshot,
    bar_series: 'BarSeries',
) -> None:
    """Print summary + factors using the standard test output format."""
    if TEST_LOOKBACK_END_TIME is None:
        lt = 'full_session'
    else:
        lt_wall = datetime.combine(session_date, TEST_LOOKBACK_END_TIME, tzinfo=local_zone())
        lt = lt_wall.strftime('%H:%M:%S %Z')
    print(f'{symbol} | {session_date} | trend_exhaustion_reversal = {triggered} | lookback_end_local = {lt}')
    print(f'compression_3bar_block_last_bar_local={_compression_last_bar_clock_local(bar_series, setup)}')
    if setup is None:
        probe = trend_exhaustion_probe_last_bar(bar_series)
        rev = trend_exhaustion_reversal_bar_probe_last(bar_series)
        loc_str = (
            f'{probe.trigger_close_location:.4f}'
            if probe.trigger_close_location is not None
            else 'n/a'
        )
        pri_str = f'{rev.prior_range_atr:.4f}' if rev.prior_range_atr is not None else 'n/a'
        vs_str = (
            f'{rev.trigger_range_vs_prior:.4f}' if rev.trigger_range_vs_prior is not None else 'n/a'
        )
        core_gates = probe.compression_ok and probe.trigger_ok and rev.reversal_bar_ok
        print(f'compression_bars={COMPRESSION_BARS} | {probe.compression_ok}')
        print(
            f'trigger_close_location={loc_str} >= {TRIGGER_CLOSE_LOCATION_MIN:.2f} | '
            f'{probe.trigger_ok}'
        )
        print(
            f'reversal_bar_prior_range_atr={pri_str} >= {REVERSAL_BAR_MIN_PRIOR_RANGE_ATR:.2f} | '
            f'{rev.prior_range_meets_atr}'
        )
        print(
            f'reversal_bar_trigger_range_vs_prior={vs_str} >= '
            f'{REVERSAL_BAR_MIN_TRIGGER_RANGE_FRAC:.2f} | {rev.trigger_range_meets_prior_frac}'
        )
        print(f'reversal_bar | {rev.reversal_bar_ok}')
        print(
            f'reversal_bar trigger in last {REVERSAL_BAR_LOOKBACK_BARS} bars | '
            f'{rev.trigger_in_lookback}'
        )
        hv_str = (
            str(rev.high_volume_bar_index) if rev.high_volume_bar_index is not None else 'n/a'
        )
        bull_str = (
            str(rev.reversal_pair_bull_bar_index)
            if rev.reversal_pair_bull_bar_index is not None
            else 'n/a'
        )
        print(
            f'reversal_hv_bar_index={hv_str} pair_prior_idx={rev.prior_bar_index} '
            f'pair_bull_idx={bull_str} (scan hv→session_trigger, '
            f'<= {MAX_BARS_SINCE_LAST_TREND_CHANGE} bars since tc)'
        )
        pct_ok = probe.compression_percentile_max <= VOLUME_PERCENTILE_MAX
        print(
            f'compression_percentile_max={probe.compression_percentile_max:.2f} '
            f'<= {VOLUME_PERCENTILE_MAX:.2f} | {pct_ok}'
        )
        print(f'volume_ma_period | {VOLUME_MA_PERIOD}')
        print(f'last_bar compression+trigger+reversal | {core_gates}')
        bars_since_text = (
            str(snapshot.bars_since_last_trend_change)
            if snapshot.bars_since_last_trend_change is not None
            else 'n/a'
        )
        travel_text = (
            f'{snapshot.travel_from_trend_change_atr:.4f}'
            if snapshot.travel_from_trend_change_atr is not None
            else 'n/a'
        )
        print(
            f'atr_reference bars_since_last_trend_change={bars_since_text} '
            f'<= {MAX_BARS_SINCE_LAST_TREND_CHANGE} | {snapshot.bars_since_passes}'
        )
        print(
            f'atr_reference travel_from_last_trend_change_atr={travel_text} '
            f'>= {MIN_TRAVEL_FROM_LAST_TREND_CHANGE_ATR:.2f} | {snapshot.travel_passes}'
        )
        _print_full_scan_gates_last_bar(bar_series)
        return

    compression_bars = int(setup.compression_bars)
    trigger_close_location = float(setup.trigger_close_location)
    ema9_at_trigger = float(setup.ema9_at_trigger)
    ema21_at_trigger = float(setup.ema21_at_trigger)
    trigger_bar_index = int(setup.trigger_bar_index)
    first_upturn_idx = setup.ema9_first_upturn_bar_index
    vwap_at_trigger = setup.vwap_at_trigger
    compression_percentile_max = float(setup.compression_percentile_max)
    atr_value = setup.atr_value
    last_trend_change_price = float(setup.last_trend_change_price)
    travel_from_trend_change_atr = setup.travel_from_trend_change_atr
    last_trend_change_direction = setup.last_trend_change_direction
    bars_since_last_trend_change = int(setup.bars_since_last_trend_change)
    reversal_prior_atr = float(setup.reversal_prior_range_atr)
    reversal_vs_prior = float(setup.reversal_trigger_range_vs_prior)

    in_range = compression_bars == COMPRESSION_BARS
    close_loc_ok = trigger_close_location >= TRIGGER_CLOSE_LOCATION_MIN
    first_upturn_ok = (
        first_upturn_idx is not None and first_upturn_idx == trigger_bar_index - 1
    )
    bars_since_ref = bars_since_last_trend_change <= MAX_BARS_SINCE_LAST_TREND_CHANGE
    travel_ref = travel_from_trend_change_atr >= MIN_TRAVEL_FROM_LAST_TREND_CHANGE_ATR
    rev_prior_ok = reversal_prior_atr >= REVERSAL_BAR_MIN_PRIOR_RANGE_ATR
    rev_frac_ok = reversal_vs_prior >= REVERSAL_BAR_MIN_TRIGGER_RANGE_FRAC
    print(f'compression_bars={compression_bars} | {in_range}')
    print(f'trigger_close_location={trigger_close_location:.4f} >= {TRIGGER_CLOSE_LOCATION_MIN:.2f} | {close_loc_ok}')
    print(
        f'reversal_bar_prior_range_atr={reversal_prior_atr:.4f} >= '
        f'{REVERSAL_BAR_MIN_PRIOR_RANGE_ATR:.2f} | {rev_prior_ok}'
    )
    print(
        f'reversal_bar_trigger_range_vs_prior={reversal_vs_prior:.4f} >= '
        f'{REVERSAL_BAR_MIN_TRIGGER_RANGE_FRAC:.2f} | {rev_frac_ok}'
    )
    rev_lookback_ok = trigger_bar_index >= len(bar_series.bars_2min) - REVERSAL_BAR_LOOKBACK_BARS
    print('reversal_bar | True')
    print(
        f'reversal_bar trigger in last {REVERSAL_BAR_LOOKBACK_BARS} bars | {rev_lookback_ok}'
    )
    pct_ok = compression_percentile_max <= VOLUME_PERCENTILE_MAX
    print(
        f'compression_percentile_max={compression_percentile_max:.2f} <= {VOLUME_PERCENTILE_MAX:.2f} '
        f'| {pct_ok}'
    )
    print(f'volume_ma_period | {VOLUME_MA_PERIOD}')
    upturn_str = 'n/a' if first_upturn_idx is None else str(first_upturn_idx)
    print(
        f'first_ema9_upturn_idx={upturn_str} equals trigger_idx-1={trigger_bar_index - 1} '
        f'(informational) | {first_upturn_ok}'
    )
    print(
        f'ema9@{trigger_bar_index - 1}={ema9_at_trigger:.2f} '
        f'ema21@{trigger_bar_index - 1}={ema21_at_trigger:.2f}'
    )
    vwap_str = f'{vwap_at_trigger:.2f}' if vwap_at_trigger is not None else 'n/a'
    print(f'vwap@{trigger_bar_index - 1}={vwap_str}')
    print(
        f'atr_reference bars_since_last_trend_change={bars_since_last_trend_change} '
        f'<= {MAX_BARS_SINCE_LAST_TREND_CHANGE} | {bars_since_ref}'
    )
    travel_str = f'{travel_from_trend_change_atr:.4f}'
    print(
        f'atr_reference travel_from_last_trend_change_atr={travel_str} '
        f'>= {MIN_TRAVEL_FROM_LAST_TREND_CHANGE_ATR:.2f} | {travel_ref}'
    )
    atr_str = f'{atr_value:.4f}' if atr_value is not None else 'n/a'
    print(
        f'last_trend_change price={last_trend_change_price:.2f} '
        f'direction={last_trend_change_direction} atr_daily={atr_str}'
    )


class TestTrendExhaustionReversalIntegration(unittest.TestCase):
    """Integration tests against real IB historical data."""

    ib = None
    bundle = None

    @classmethod
    def setUpClass(cls) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=DeprecationWarning)
            cls.ib = connect(readonly=True)
        assert cls.ib is not None and cls.ib.isConnected()
        cls.bundle = load_bars(cls.ib, SYMBOL, end_date=TEST_SESSION_DATE)

    @classmethod
    def tearDownClass(cls) -> None:
        disconnect(cls.ib)

    def test_trend_exhaustion_reversal(self) -> None:
        """Run scan and print factors when a setup matches."""
        bundle = self.bundle
        if bundle is None or not bundle.bars_2min:
            self.skipTest('No 2-minute bars returned')
        assert bundle is not None

        session_day = bundle.bars_2min[0].date.date()
        bundle_scan = _bar_series_2min_through_end_time(
            bundle,
            TEST_LOOKBACK_END_TIME,
            session_date=session_day,
        )
        if not bundle_scan.bars_2min:
            self.skipTest('No 2-minute bars through TEST_LOOKBACK_END_TIME')
        result_bool = trend_exhaustion_reversal(bundle_scan)
        result_scan = trend_exhaustion_reversal_scan(bundle_scan)
        self.assertEqual(result_bool, result_scan.triggered)

        _print_scan_report(
            SYMBOL,
            session_day,
            result_scan.triggered,
            result_scan.first_setup,
            trend_exhaustion_context_snapshot(bundle_scan),
            bundle_scan,
        )

        self.assertIsInstance(result_scan.triggered, bool)


if __name__ == '__main__':
    unittest.main(buffer=False)
