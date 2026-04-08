"""Integration tests for breakout bar pattern scans.

Requires TWS or IB Gateway running with API enabled.
Change SYMBOL to test a different ticker.
shell cmd
uv run python -m tests.test_breakout
"""

import time
import unittest
import warnings

from strategies.bar_patterns.breakout import BREAK_OUT_LOOKBACK_BARS
from strategies.bar_patterns.breakout import break_out_bar
from strategies.bar_patterns.breakout import break_out_bar_stats
from strategies.bar_patterns.breakout import breakout_limit_entry_price
from strategies.utils import last_trading_day
from trading.bar_loader import load_bars
from trading.market_data import connect
from trading.market_data import disconnect
from trading.models import TickerQuote

SYMBOL = 'SOC'
# Override for quick experiments; set to None to use strategy default.
LOOKBACK_BARS_OVERRIDE: int | None = 130

MIDPOINT = 13.30
ASK_ABOVE = 15.35
ASK_BELOW = 12.50
BID_BELOW = 12.40
BID_ABOVE = 14.00


class TestBreakoutLimitEntryPrice(unittest.TestCase):
    """Unit tests for breakout limit vs bid/ask (no IB)."""

    def test_long_uses_min_of_midpoint_and_ask_when_ask_higher(self) -> None:
        quote = TickerQuote(bid=13.0, ask=ASK_ABOVE)
        self.assertEqual(
            breakout_limit_entry_price(MIDPOINT, True, quote),
            round(min(MIDPOINT, ASK_ABOVE), 2),
        )

    def test_long_uses_min_when_ask_below_midpoint(self) -> None:
        quote = TickerQuote(bid=12.0, ask=ASK_BELOW)
        self.assertEqual(breakout_limit_entry_price(MIDPOINT, True, quote), ASK_BELOW)

    def test_short_uses_max_of_midpoint_and_bid_when_bid_lower(self) -> None:
        quote = TickerQuote(bid=BID_BELOW, ask=13.0)
        self.assertEqual(breakout_limit_entry_price(MIDPOINT, False, quote), MIDPOINT)

    def test_short_uses_max_when_bid_above_midpoint(self) -> None:
        quote = TickerQuote(bid=BID_ABOVE, ask=15.0)
        self.assertEqual(breakout_limit_entry_price(MIDPOINT, False, quote), BID_ABOVE)

    def test_no_quote_returns_rounded_midpoint(self) -> None:
        self.assertEqual(breakout_limit_entry_price(MIDPOINT, True, None), MIDPOINT)
        self.assertEqual(breakout_limit_entry_price(MIDPOINT, False, None), MIDPOINT)


class TestBreakoutIntegration(unittest.TestCase):
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
        cls.bundle = load_bars(cls.ib, SYMBOL)

    @classmethod
    def tearDownClass(cls) -> None:
        """Disconnect after all tests."""
        disconnect(cls.ib)

    def test_break_out_bar_timing_and_result(self) -> None:
        """Fetch 2-min bars via bar_loader, run break_out_bar, and print timing metrics."""
        lookback_bars = LOOKBACK_BARS_OVERRIDE or BREAK_OUT_LOOKBACK_BARS
        session_date = last_trading_day()
        bundle = self.bundle
        retrieval_seconds = 0.0

        if bundle is None or not bundle.bars_2min:
            self.skipTest('No 2-minute bars returned')
        assert bundle is not None

        if len(bundle.bars_2min_rth) < lookback_bars:
            self.skipTest(
                f'Need at least {lookback_bars} 2-minute RTH bars for session {session_date} but only got '
                f'{len(bundle.bars_2min_rth)}'
            )

        function_start = time.perf_counter()
        result = break_out_bar(bundle, lookback_bars=lookback_bars)
        stats = break_out_bar_stats(bundle, lookback_bars=lookback_bars)
        largest_bar_size = stats.largest_bar_size
        avg_bar_size = stats.avg_bar_size
        midpoint_largest = stats.midpoint_of_breakout_bar
        if largest_bar_size is None or avg_bar_size is None:
            self.skipTest('Unable to calculate bar-size stats from retrieved data')
        function_seconds = time.perf_counter() - function_start
        total_seconds = retrieval_seconds + function_seconds

        self.assertIsInstance(result, bool)
        self.assertEqual(result, stats.breakout)
        mid_str = f' | midpoint_largest={midpoint_largest:.2f}' if midpoint_largest is not None else ''
        print(
            f'{SYMBOL} break_out_bar={result} | session_date={session_date} | '
            f'avg_2min_bar_size={avg_bar_size:.2f} | largest_bar_size={largest_bar_size:.2f}{mid_str} | '
            f'lookback_bars={lookback_bars} | bars_session={len(bundle.bars_2min_rth)} | '
            f'total_s={total_seconds:.4f} | retrieval_s={retrieval_seconds:.4f} | function_s={function_seconds:.6f}'
        )


if __name__ == '__main__':
    warnings.filterwarnings('ignore', category=DeprecationWarning, module='ib_async')
    unittest.main(buffer=False)
