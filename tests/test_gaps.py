"""Integration tests for gap pattern scans.

Requires TWS or IB Gateway running with API enabled.
Change SYMBOL to test a different ticker.
shell cmd
uv run python -m tests.test_gaps
"""

import time
import unittest
from datetime import date
from datetime import datetime

from strategies.bar_patterns.gaps import day_3_gap
from trading.bar_loader import load_bars
from trading.market_data import connect
from trading.market_data import disconnect

SYMBOL = 'USO'


def _bar_date(bar_date_value: object) -> date | None:
    """Normalize bar date value to date."""
    if isinstance(bar_date_value, datetime):
        return bar_date_value.date()
    if isinstance(bar_date_value, date):
        return bar_date_value
    return None


class TestGapsIntegration(unittest.TestCase):
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

    def test_day_3_gap_timing_and_result(self) -> None:
        """Fetch daily bars via bar_loader, run day_3_gap, and print timing metrics."""
        retrieval_start = time.perf_counter()
        bundle = load_bars(self.ib, SYMBOL)
        retrieval_seconds = time.perf_counter() - retrieval_start

        if bundle is None or not bundle.bars_1d:
            self.skipTest('No daily bars returned')
        assert bundle is not None

        function_start = time.perf_counter()
        result = day_3_gap(bundle)
        function_seconds = time.perf_counter() - function_start
        total_seconds = retrieval_seconds + function_seconds

        self.assertIsInstance(result, bool)
        latest_day = _bar_date(bundle.bars_1d[-1].date)
        print(
            f'{SYMBOL} day_3_gap={result} | bars={len(bundle.bars_1d)} | '
            f'latest_day={latest_day} | retrieval_s={retrieval_seconds:.4f} | '
            f'function_s={function_seconds:.6f} | total_s={total_seconds:.4f}'
        )


if __name__ == '__main__':
    unittest.main(buffer=False)
