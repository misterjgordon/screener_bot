"""Integration tests for Fashionably Late bar pattern (9 EMA cross vs VWAP, rel vol > 2).

Requires TWS or IB Gateway running with API enabled. Use --date YYYY-MM-DD to run for a
specific session; if omitted, uses last available date (most recent data from IB).
shell cmd
uv run python -m tests.test_fashionably_late
uv run python -m tests.test_fashionably_late --date 2026-03-09
"""

import sys
import time
import unittest
import warnings
from datetime import date

from strategies.bar_patterns.fashionably_late import MIN_BARS_2MIN
from strategies.bar_patterns.fashionably_late import TIME_WINDOW_END
from strategies.bar_patterns.fashionably_late import TIME_WINDOW_START
from strategies.bar_patterns.fashionably_late import fashionably_late
from strategies.bar_patterns.fashionably_late import fashionably_late_diagnostics
from strategies.bar_patterns.fashionably_late import fashionably_late_stats
from strategies.utils import last_trading_day
from trading.bar_loader import load_bars
from trading.market_data import connect
from trading.market_data import disconnect

SYMBOL = 'ORCL'


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
                parts = argv[i + 1].split('-')
                if len(parts) == 3:
                    result = date(int(parts[0]), int(parts[1]), int(parts[2]))
                i += 2
                continue
            except (ValueError, IndexError, TypeError):
                pass
        if a.startswith('--date='):
            try:
                part = a.split('=', 1)[1]
                parts = part.split('-')
                if len(parts) == 3:
                    result = date(int(parts[0]), int(parts[1]), int(parts[2]))
                i += 1
                continue
            except (ValueError, IndexError, TypeError):
                pass
        new_argv.append(a)
        i += 1
    sys.argv = [sys.argv[0]] + new_argv
    return result


_TEST_DATE: date | None = _parse_and_strip_date()


class TestFashionablyLateIntegration(unittest.TestCase):
    """Integration tests for Fashionably Late pattern against real IB historical data."""

    ib = None
    bundle = None

    @classmethod
    def setUpClass(cls) -> None:
        """Connect once and load bars once for all tests."""
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=DeprecationWarning)
            cls.ib = connect(readonly=True)
        assert cls.ib is not None and cls.ib.isConnected()
        cls.bundle = load_bars(cls.ib, SYMBOL, end_date=_TEST_DATE)

    @classmethod
    def tearDownClass(cls) -> None:
        """Disconnect after all tests."""
        disconnect(cls.ib)

    def test_fashionably_late_timing_and_result(self) -> None:
        """Fetch 2-min bars via bar_loader, run fashionably_late, and print timing metrics."""
        session_date = _TEST_DATE or last_trading_day()
        bundle = self.bundle

        if bundle is None or not bundle.bars_2min:
            self.skipTest('No 2-minute bars returned')
        assert bundle is not None
        retrieval_seconds = 0.0

        if len(bundle.bars_2min) < 11:
            self.skipTest(
                f'Need at least 11 2-minute bars for session {session_date} but only got '
                f'{len(bundle.bars_2min)}'
            )

        function_start = time.perf_counter()
        result = fashionably_late(bundle)
        stats = fashionably_late_stats(bundle)
        function_seconds = time.perf_counter() - function_start
        total_seconds = retrieval_seconds + function_seconds

        self.assertIsInstance(result, bool)
        self.assertEqual(result, stats.triggered)

        rvol_str = f' rvol={stats.rvol_at_cross:.2f}' if stats.rvol_at_cross is not None else ''
        print(
            f'{SYMBOL} fashionably_late={result} | session_date={session_date} | {rvol_str} | '
            f'total_s={total_seconds:.4f} | retrieval_s={retrieval_seconds:.4f} | function_s={function_seconds:.6f}'
        )
        if stats.crosses:
            for c in stats.crosses:
                t_str = c.cross_bar_time.strftime('%H:%M') if c.cross_bar_time else ''
                print(
                    f'  {c.direction} cross_price={c.cross_price:.2f} cross_time={t_str} '
                    f'stop={c.stop_price:.2f} target={c.target_price:.2f}'
                )

    def test_fashionably_late_diagnostics(self) -> None:
        """Run diagnostics and print which factors are not true (why no trigger)."""
        session_date = _TEST_DATE or last_trading_day()
        bundle = self.bundle
        if bundle is None or not bundle.bars_2min:
            self.skipTest('No 2-minute bars returned')
        assert bundle is not None

        d = fashionably_late_diagnostics(bundle)

        not_ok: list[str] = []
        ok: list[str] = []

        if d.enough_bars:
            ok.append(f'enough_bars (bars={d.bars_count} >= {MIN_BARS_2MIN})')
        else:
            not_ok.append(f'enough_bars: bars={d.bars_count} (need >= {MIN_BARS_2MIN})')

        if d.daily_rvol is not None:
            if d.rvol_ok:
                ok.append(f'daily_rvol >= rvol_min ({d.daily_rvol:.2f} >= {d.rvol_min})')
            else:
                not_ok.append(f'daily_rvol >= rvol_min: rvol={d.daily_rvol:.2f} (need >= {d.rvol_min})')
        else:
            not_ok.append('daily_rvol: None (not enough daily bars)')

        if d.long_cross_found:
            ok.append('long: 9 EMA cross above VWAP found')
            if d.long_upsloping_ema is False:
                not_ok.append('long: upsloping 9 EMA (at first cross bar)')
            else:
                ok.append('long: upsloping 9 EMA')
            if d.long_flat_to_down_vwap is False:
                not_ok.append('long: flat-to-downsloping VWAP (at first cross bar)')
            else:
                ok.append('long: flat-to-down VWAP')
            if d.long_time_in_window is False:
                not_ok.append(f'long: bar time in window [{TIME_WINDOW_START}, {TIME_WINDOW_END}]')
            elif d.long_time_in_window is True:
                ok.append('long: time in window')
            if d.long_move_to_vwap_ok is False:
                not_ok.append('long: move from trailing low to VWAP >= 0.3 ADR')
            elif d.long_move_to_vwap_ok is True:
                ok.append('long: move to VWAP >= 0.3 ADR')
        else:
            not_ok.append('long: no 9 EMA cross above VWAP')

        if d.short_cross_found:
            ok.append('short: 9 EMA cross below VWAP found')
            if d.short_downsloping_ema is False:
                not_ok.append('short: downsloping 9 EMA (at first cross bar)')
            else:
                ok.append('short: downsloping 9 EMA')
            if d.short_flat_to_up_vwap is False:
                not_ok.append('short: flat-to-upsloping VWAP (at first cross bar)')
            else:
                ok.append('short: flat-to-up VWAP')
            if d.short_time_in_window is False:
                not_ok.append(f'short: bar time in window [{TIME_WINDOW_START}, {TIME_WINDOW_END}]')
            elif d.short_time_in_window is True:
                ok.append('short: time in window')
            if d.short_move_to_vwap_ok is False:
                not_ok.append('short: move from trailing high to VWAP >= 0.3 ADR')
            elif d.short_move_to_vwap_ok is True:
                ok.append('short: move to VWAP >= 0.3 ADR')
        else:
            not_ok.append('short: no 9 EMA cross below VWAP')

        print(f'Fashionably Late diagnostics: {SYMBOL} | session_date={session_date}')
        print()
        print('Factors NOT true (why no trigger):')
        for s in not_ok:
            print(f'  - {s}')
        if not not_ok:
            print('  (none; all factors passed)')
        print()
        print('Factors true:')
        for s in ok:
            print(f'  + {s}')

        if d.long_cross_found and d.long_cross_price is not None:
            trailing = f' trailing_low={d.long_trailing_low:.2f}' if d.long_trailing_low is not None else ''
            move = f' move_to_vwap={d.long_move_to_vwap:.2f}' if d.long_move_to_vwap is not None else ''
            adr_ratio = (
                f' ({d.long_move_to_vwap / d.adr:.2f} ADR)'
                if d.adr and d.adr > 0 and d.long_move_to_vwap is not None
                else ''
            )
            print(f'  long: cross_price={d.long_cross_price:.2f}{trailing}{move}{adr_ratio}')
        if d.short_cross_found and d.short_cross_price is not None:
            trailing = (
                f' trailing_high={d.short_trailing_high:.2f}' if d.short_trailing_high is not None else ''
            )
            move = (
                f' move_to_vwap={d.short_move_to_vwap:.2f}' if d.short_move_to_vwap is not None else ''
            )
            adr_ratio = (
                f' ({d.short_move_to_vwap / d.adr:.2f} ADR)'
                if d.adr and d.adr > 0 and d.short_move_to_vwap is not None
                else ''
            )
            print(f'  short: cross_price={d.short_cross_price:.2f}{trailing}{move}{adr_ratio}')


if __name__ == '__main__':
    warnings.filterwarnings('ignore', category=DeprecationWarning, module='ib_async')
    unittest.main(buffer=False)
