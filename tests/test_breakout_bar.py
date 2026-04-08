"""Integration tests for breakout bar: realtime 5-sec bars and breakout pattern (true/false, long/short).

Requires TWS or IB Gateway running with API enabled.
Change SYMBOL to test a different ticker.
shell cmd
uv run python -m tests.test_breakout_bar
"""

import time
import unittest
import warnings

from ib_async import Stock

from strategies.bar_patterns.breakout import BREAK_OUT_LOOKBACK_BARS
from strategies.bar_patterns.breakout import _synthetic_bar
from strategies.bar_patterns.breakout import break_out_bar_stats
from trading.bar_loader import load_bars
from trading.config import ACCOUNT_CURRENCY
from trading.market_data import connect
from trading.market_data import disconnect

SYMBOL = 'NVDA'
WHAT_TO_SHOW = 'TRADES'
USE_RTH = True
MAX_WAIT_SECONDS = 1.0
POLL_INTERVAL_SECONDS = 0.25


class TestRealtimeBarIntegration(unittest.TestCase):
    """Integration test against real IB realtimeBar stream."""

    ib = None
    bundle = None

    @classmethod
    def setUpClass(cls) -> None:
        """Connect once for all tests; load bars once for both tests."""
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=DeprecationWarning)
            cls.ib = connect(readonly=True)
        assert cls.ib is not None and cls.ib.isConnected()
        cls.bundle = load_bars(cls.ib, SYMBOL)

    @classmethod
    def tearDownClass(cls) -> None:
        """Disconnect after all tests."""
        disconnect(cls.ib)

    def test_realtime_bar_returns_values(self) -> None:
        """Build synthetic bar (last 2-min + 5-sec realtime) and print it."""
        assert self.ib is not None
        bundle = self.bundle
        if bundle is None:
            self.skipTest(f'No bar bundle for {SYMBOL}')
        assert bundle is not None
        if not bundle.bars_2min_rth:
            self.skipTest(f'No RTH 2-min bars for {SYMBOL}')

        contract = Stock(SYMBOL, 'SMART', ACCOUNT_CURRENCY)
        self.ib.qualifyContracts(contract)
        bars = self.ib.reqRealTimeBars(
            contract=contract,
            barSize=5,
            whatToShow=WHAT_TO_SHOW,
            useRTH=USE_RTH,
        )
        start = time.perf_counter()
        while not bars and (time.perf_counter() - start) < MAX_WAIT_SECONDS:
            self.ib.sleep(POLL_INTERVAL_SECONDS)

        if not bars:
            self.skipTest('No realtime bars received from IB within timeout')

        self.ib.cancelRealTimeBars(bars)
        last_2min = bundle.bars_2min_rth[-1]
        rt_bar = bars[-1]
        synthetic = _synthetic_bar(last_2min, rt_bar)

        self.assertIsNotNone(synthetic.open)
        self.assertIsNotNone(synthetic.high)
        self.assertIsNotNone(synthetic.low)
        self.assertIsNotNone(synthetic.close)

        print(
            f'{SYMBOL} synthetic bar: '
            f'time={synthetic.date} '
            f'open={synthetic.open} '
            f'high={synthetic.high} '
            f'low={synthetic.low} '
            f'close={synthetic.close} '
            f'volume={synthetic.volume}'
        )

    def test_breakout_bar_true_and_long_or_short(self) -> None:
        """Load 2-min bars, run breakout stats with realtime merge, assert breakout flag and long/short."""
        assert self.ib is not None
        bundle = self.bundle
        if bundle is None:
            self.skipTest(f'No bar bundle for {SYMBOL}')
        assert bundle is not None
        if len(bundle.bars_2min_rth) < BREAK_OUT_LOOKBACK_BARS:
            self.skipTest(
                f'Need at least {BREAK_OUT_LOOKBACK_BARS} RTH 2-min bars for {SYMBOL}'
            )

        stats = break_out_bar_stats(
            bundle,
            lookback_bars=BREAK_OUT_LOOKBACK_BARS,
            ib=self.ib,
            symbol=SYMBOL,
        )

        self.assertIsInstance(stats.breakout, bool)
        if stats.breakout:
            self.assertIsNotNone(stats.breakout_bar_bullish)
            self.assertIsNotNone(stats.midpoint_of_breakout_bar)
            side = 'long' if stats.breakout_bar_bullish else 'short'
        else:
            self.assertIsNone(stats.breakout_bar_bullish)
            side = 'none'

        print(
            f'{SYMBOL} breakout={stats.breakout} '
            f'side={side} '
            f'midpoint={stats.midpoint_of_breakout_bar} '
            f'largest_bar_size={stats.largest_bar_size} '
            f'avg_bar_size={stats.avg_bar_size}'
        )


if __name__ == '__main__':
    warnings.filterwarnings('ignore', category=DeprecationWarning, module='ib_async')
    unittest.main(buffer=False)
