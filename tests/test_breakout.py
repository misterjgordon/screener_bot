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
from strategies.utils import last_trading_day
from trading.bar_loader import load_bars
from trading.market_data import connect
from trading.market_data import disconnect

SYMBOL = 'IBIT'
# Override for quick experiments; set to None to use strategy default.
LOOKBACK_BARS_OVERRIDE: int | None = 130


class TestBreakoutIntegration(unittest.TestCase):
    """Integration tests against real IB historical data."""

    ib = None

    @classmethod
    def setUpClass(cls) -> None:
        """Connect once for all tests."""
        cls.ib = connect(readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        """Disconnect after all tests."""
        disconnect(cls.ib)

    def setUp(self) -> None:
        """Skip tests if IB not connected."""
        if self.ib is None or not self.ib.isConnected():
            self.skipTest('IB not connected - is TWS/Gateway running?')

    def test_break_out_bar_timing_and_result(self) -> None:
        """Fetch 2-min bars via bar_loader, run break_out_bar, and print timing metrics."""
        lookback_bars = LOOKBACK_BARS_OVERRIDE or BREAK_OUT_LOOKBACK_BARS
        session_date = last_trading_day()
        retrieval_start = time.perf_counter()
        bundle = load_bars(self.ib, SYMBOL)
        retrieval_seconds = time.perf_counter() - retrieval_start

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
