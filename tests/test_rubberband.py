"""Run ``rubberband_scan`` on IB data for ``test_symbol`` and ``test_session_date``.

Bars come only from ``load_bars``. Prints follow ``python_tests.mdc`` (summary line + factors).

shell cmd
uv run --frozen python -m tests.test_rubberband
"""

import unittest
import warnings
from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING

from strategies.bar_patterns.rubberband import MIN_SNAP_OPEN_LEG_ATR
from strategies.bar_patterns.rubberband import RubberbandFactors
from strategies.bar_patterns.rubberband import RubberbandMissDiagnosis
from strategies.bar_patterns.rubberband import RubberbandScanResult
from strategies.bar_patterns.rubberband import bar_series_with_synthetic_2min_tail
from strategies.bar_patterns.rubberband import last_loaded_2min_bar_is_incomplete
from strategies.bar_patterns.rubberband import rubberband_match_ok
from strategies.bar_patterns.rubberband import rubberband_scan
from strategies.utils import last_trading_day
from trading.bar_loader import load_bars
from trading.market_data import connect
from trading.market_data import disconnect
from trading.market_data import get_ticker_quote

if TYPE_CHECKING:
    from trading.models import BarSeries
    from trading.models import TickerQuote

test_symbol = 'GLD'
test_session_date: date | None = date(2026, 3, 25)

# Optional snap-bar gate after session extension: open leg >= this × ATR. 0 = skip (default in
# ``rubberband``); use ``diagnose_all_top_bars`` to print factor rows for each top-N bar.
SCAN_MIN_SNAP_OPEN_LEG_ATR = MIN_SNAP_OPEN_LEG_ATR


def _fmt_factor_num(v: float | None) -> str:
    if v is None:
        return 'n/a'
    s = f'{float(v):.6f}'.rstrip('0').rstrip('.')
    return s if s else '0'


def _print_miss_diagnoses(rows: tuple[RubberbandMissDiagnosis, ...]) -> None:
    """Print miss rows; bracket keys match ``RubberbandMissDiagnosis`` / ``RubberbandFactors`` names."""
    for row in rows:
        print(f'[code] = {row.code}')
        if row.snap_bar_time is not None:
            t = row.snap_bar_time.time() if isinstance(row.snap_bar_time, datetime) else row.snap_bar_time
            print(f'[snap_bar_time] = {t}')
        if row.rth_bar_index is not None:
            print(f'[rth_bar_index] = {row.rth_bar_index}')
        if row.direction is not None:
            print(f'[direction] = {row.direction}')
        if row.down_from_open_atr is not None:
            print(f'[down_from_open_atr] = {row.down_from_open_atr}')
        if row.up_from_open_atr is not None:
            print(f'[up_from_open_atr] = {row.up_from_open_atr}')
        if row.failed_match_gate_names:
            joined = ','.join(row.failed_match_gate_names)
            print(f'[failed_match_gate_names] = {joined}')
        if row.detail:
            print(f'[detail] = {row.detail}')
        if row.factors is not None:
            _print_rubberband_factors(row.factors)
        print()


def _print_rubberband_factors(f: RubberbandFactors) -> None:
    """One line per gate: metric vs threshold | value op threshold = pass (see ``ATR_EXTENSION_MIN`` etc.)."""
    print(
        f'[open_leg_atr] >= [ATR_EXTENSION_MIN] | '
        f'{_fmt_factor_num(f.open_leg_atr)} >= {_fmt_factor_num(f.min_open_leg_atr)} = {f.meets_min_open_leg_atr}',
    )
    print(
        f'[relative_volume] >= [RVOL_MIN] | '
        f'{
            _fmt_factor_num(
                f.relative_volume)} >= {
            _fmt_factor_num(
                f.relative_volume_min)} = {
                    f.meets_relative_volume_min}',
    )
    print(
        f'[this_bar_range] > [prior_bar_range] | '
        f'{_fmt_factor_num(f.this_bar_range)} > {_fmt_factor_num(f.prior_bar_range)} = {f.this_bar_range_gt_prior}',
    )
    if f.min_snap_open_leg_atr > 0:
        snap_ok = (
            f.snap_open_leg_atr is not None
            and f.snap_open_leg_atr >= f.min_snap_open_leg_atr
        )
        print(
            f'[snap_open_leg_atr] >= [MIN_SNAP_OPEN_LEG_ATR] | '
            f'{_fmt_factor_num(f.snap_open_leg_atr)} >= {_fmt_factor_num(f.min_snap_open_leg_atr)} = {snap_ok}',
        )
    else:
        print(
            f'[snap_open_leg_atr] | {_fmt_factor_num(f.snap_open_leg_atr)} '
            f'(MIN_SNAP_OPEN_LEG_ATR=0, no gate)',
        )
    print(f'[atr_change] | {_fmt_factor_num(f.atr_change)}')
    print(f'[regular_session_open] | {_fmt_factor_num(f.regular_session_open)}')
    print(f'[match_ok] = {rubberband_match_ok(f)}')


def print_rubberband_report(
    label: str,
    result: RubberbandScanResult,
    *,
    include_session_date: bool,
) -> None:
    """Print rubberband summary then factors.

    When ``include_session_date`` is True, summary is ``[label] {session} rubberband = ...``.
    When False, the opening ``[symbol] {session}`` line already set session — summary omits the date.

    When there is no setup, prints ``[rubberband_no_bar_passed_all_gates] = true`` and, if
    ``result.miss_diagnoses`` is set, one block per attempt using field names as bracket keys.
    When a setup exists and ``miss_diagnoses`` is still set (``diagnose_all_top_bars``), prints the
    winning setup first, then per-bar diagnostic blocks.
    """
    exists = result.exists and result.setup is not None
    if include_session_date:
        print(f'[{label}] {result.session_date} rubberband = {exists}')
    else:
        print(f'[{label}] rubberband = {exists}')
    if not exists:
        print('[qualifying_setup] = false')
        print('[rubberband_no_bar_passed_all_gates] = true')
        if result.miss_diagnoses:
            _print_miss_diagnoses(result.miss_diagnoses)
        return
    s = result.setup
    assert s is not None
    print('[qualifying_setup] = true')
    t = s.snap_bar_time.time() if isinstance(s.snap_bar_time, datetime) else s.snap_bar_time
    print(f'[direction] = {s.direction}')
    print(f'[snap_bar_time] = {t}')
    print(f'[entry_reference] = {s.entry_reference}')
    print(f'[stop_price] = {s.stop_price}')
    _print_rubberband_factors(s.factors)
    if result.miss_diagnoses:
        print('[rubberband_diagnose_all_top_bars] = true')
        _print_miss_diagnoses(result.miss_diagnoses)


def run_scan(
    bundle: 'BarSeries',
    session: date,
    label: str,
    *,
    include_session_date: bool = True,
    min_snap_open_leg_atr: float = SCAN_MIN_SNAP_OPEN_LEG_ATR,
    diagnose_all_top_bars: bool = True,
) -> None:
    """Run ``rubberband_scan`` and print report."""
    result = rubberband_scan(
        bundle,
        session_date=session,
        with_miss_diagnoses=True,
        min_snap_open_leg_atr=min_snap_open_leg_atr,
        diagnose_all_top_bars=diagnose_all_top_bars,
    )
    print_rubberband_report(label, result, include_session_date=include_session_date)


def apply_rubberband_for_symbol_session(
    symbol: str,
    bundle: 'BarSeries',
    session: date,
    *,
    ib_quote: 'TickerQuote | None',
    min_snap_open_leg_atr: float = SCAN_MIN_SNAP_OPEN_LEG_ATR,
    diagnose_all_top_bars: bool = True,
) -> None:
    """Print session once, then quote / bar-variant lines and scans (date not repeated per line)."""
    print(f'[{symbol}] {session}')
    best = ib_quote.best_price() if ib_quote is not None else None
    print(f'[quote_ib] = {best}')
    run_scan(
        bundle,
        session,
        symbol,
        include_session_date=False,
        min_snap_open_leg_atr=min_snap_open_leg_atr,
        diagnose_all_top_bars=diagnose_all_top_bars,
    )

    last_bar = bundle.bars_2min[-1]
    if (
        best is not None
        and isinstance(last_bar.date, datetime)
        and last_loaded_2min_bar_is_incomplete(last_bar)
    ):
        with_tail = bar_series_with_synthetic_2min_tail(
            bundle,
            last_price=best,
            bar_end=last_bar.date + timedelta(minutes=2),
        )
        tail_label = f'{symbol}+tail'
        print('[bars_2min] = historical plus one synthetic 2m bar from quote_ib (last bar incomplete)')
        run_scan(
            with_tail,
            session,
            tail_label,
            include_session_date=False,
            min_snap_open_leg_atr=min_snap_open_leg_atr,
            diagnose_all_top_bars=diagnose_all_top_bars,
        )


class TestRubberbandScan(unittest.TestCase):
    """``test_symbol`` + ``test_session_date`` → ``load_bars`` → ``rubberband_scan``."""

    ib = None
    loaded = None

    @classmethod
    def setUpClass(cls) -> None:
        if not test_symbol.strip():
            cls.ib = None
            cls.loaded = None
            return
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=DeprecationWarning)
            cls.ib = connect(readonly=True)
        assert cls.ib is not None and cls.ib.isConnected()
        cls.loaded = load_bars(
            cls.ib,
            test_symbol.strip().upper(),
            end_date=test_session_date,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        disconnect(cls.ib)

    def test_apply_rubberband_to_symbol_and_date(self) -> None:
        """Fetch bars for ``test_symbol`` / ``test_session_date``; run rubber-band scan; print factors."""
        if not test_symbol.strip():
            self.skipTest('Set test_symbol in tests/test_rubberband.py')
        assert self.ib is not None and self.ib.isConnected()

        bundle = self.loaded
        self.assertIsNotNone(bundle)
        assert bundle is not None

        session = test_session_date if test_session_date is not None else last_trading_day()
        sym = test_symbol.strip().upper()
        quote = get_ticker_quote(self.ib, sym)

        apply_rubberband_for_symbol_session(
            sym,
            bundle,
            session,
            ib_quote=quote,
            min_snap_open_leg_atr=SCAN_MIN_SNAP_OPEN_LEG_ATR,
            diagnose_all_top_bars=True,
        )

        result = rubberband_scan(
            bundle,
            session_date=session,
            with_miss_diagnoses=True,
            min_snap_open_leg_atr=SCAN_MIN_SNAP_OPEN_LEG_ATR,
            diagnose_all_top_bars=True,
        )
        self.assertEqual(result.session_date, session)
        self.assertIsInstance(result.exists, bool)


if __name__ == '__main__':
    unittest.main()
