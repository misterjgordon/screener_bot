"""Integration tests for BarSeries (bar_loader + models).

Requires TWS or IB Gateway running with API enabled.
Change SYMBOL to test a different ticker.
shell cmd
uv run python -m tests.test_barseries
"""

import unittest
import warnings
from typing import Protocol

import pandas as pd

from strategies.indicators.ema import ema
from strategies.indicators.ema import ema9
from strategies.indicators.ema import ema21
from strategies.indicators.vwap import vwap
from strategies.utils import last_trading_day
from trading.bar_loader import load_bars
from trading.market_data import connect
from trading.market_data import disconnect
from trading.models import BarSeries

SYMBOL = 'ibit'


class _BarOHLCV(Protocol):
    """Bar with date, OHLCV (e.g. ib_async BarData)."""

    date: object
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | int


def _bar_to_row(bar: _BarOHLCV) -> dict:
    """Extract OHLCV + date from bar to dict for DataFrame."""
    return {
        'date': bar.date,
        'open': bar.open,
        'high': bar.high,
        'low': bar.low,
        'close': bar.close,
        'volume': bar.volume,
    }


class TestBarSeriesIntegration(unittest.TestCase):
    """Integration tests against real IB historical data."""

    ib = None
    bundle = None

    @classmethod
    def setUpClass(cls) -> None:
        """Connect once and load bars once for all tests."""
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=DeprecationWarning)
            cls.ib = connect(readonly=True)
        if cls.ib is not None and cls.ib.isConnected():
            cls.bundle = load_bars(cls.ib, SYMBOL)

    @classmethod
    def tearDownClass(cls) -> None:
        """Disconnect after all tests."""
        disconnect(cls.ib)

    def setUp(self) -> None:
        """Skip tests if IB not connected."""
        if self.ib is None or not self.ib.isConnected():
            self.skipTest('IB not connected - is TWS/Gateway running?')

    def test_barseries_dataframe(self) -> None:
        """Load BarSeries for symbol and print summary + bar DataFrames."""
        bundle = self.bundle
        if bundle is None:
            self.skipTest('load_bars returned None')
        assert bundle is not None  # Narrow type after skip
        self.assertIsNotNone(bundle.bars_1d)
        self.assertIsNotNone(bundle.bars_2min)

        session_date = last_trading_day()
        summary = pd.DataFrame(
            [
                {
                    'symbol': SYMBOL,
                    'session_date': session_date,
                    'bars_1d': len(bundle.bars_1d),
                    'bars_2min': len(bundle.bars_2min),
                    'bars_2min_rth': len(bundle.bars_2min_rth),
                    'bars_2min_pm': len(bundle.bars_2min_pm),
                    'bars_2min_ah': len(bundle.bars_2min_ah),
                    'ema9': ema9(bundle),
                    'ema21': ema21(bundle),
                    'vwap': vwap(bundle),
                }
            ]
        )

        df_1d = pd.DataFrame([_bar_to_row(b) for b in bundle.bars_1d])
        bars_2min = bundle.bars_2min
        df_2min = pd.DataFrame([_bar_to_row(b) for b in bars_2min])
        df_2min = df_2min.assign(
            ema9=[ema(BarSeries(bars_1d=bundle.bars_1d, bars_2min=bars_2min[: i + 1]), 9)
                  for i in range(len(bars_2min))],
            ema21=[ema(BarSeries(bars_1d=bundle.bars_1d, bars_2min=bars_2min[: i + 1]), 21)
                   for i in range(len(bars_2min))],
            vwap=[vwap(BarSeries(bars_1d=bundle.bars_1d, bars_2min=bars_2min[: i + 1])) for i in range(len(bars_2min))],
        )

        print('\n--- BarSeries Summary ---')
        print(summary.to_string(index=False))
        print('\n--- bars_1d (last 10) ---')
        print(df_1d.tail(10).to_string(index=False))
        print('\n--- bars_2min (last 10) ---')
        print(df_2min.tail(10).to_string(index=False))


if __name__ == '__main__':
    warnings.filterwarnings('ignore', category=DeprecationWarning, module='ib_async')
    unittest.main(buffer=False)
