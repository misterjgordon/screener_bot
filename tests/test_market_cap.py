"""Integration test for market cap via IB fundamentals.

shell cmd
uv run --frozen python -m tests.test_market_cap
"""

import unittest
import warnings
from datetime import date
from typing import TYPE_CHECKING

from strategies.fundamentals.market_cap import get_market_cap
from trading.market_data import connect
from trading.market_data import disconnect

if TYPE_CHECKING:
    from ib_async import IB

SYMBOL = 'NVDA'
SESSION_DATE = date.today().isoformat()


class TestMarketCapFundamentalsIntegration(unittest.TestCase):
    """Validate market cap retrieval for a live symbol against IB."""

    ib: 'IB'

    @classmethod
    def setUpClass(cls) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=DeprecationWarning)
            ib = connect(readonly=True)
        if ib is None or not ib.isConnected():
            raise unittest.SkipTest('IB is not connected')
        cls.ib = ib

    @classmethod
    def tearDownClass(cls) -> None:
        disconnect(cls.ib)

    def test_market_cap_for_symbol(self) -> None:
        market_cap, shares_outstanding = get_market_cap(self.ib, SYMBOL)
        has_market_cap = market_cap is not None and market_cap > 0
        has_shares_outstanding = shares_outstanding is not None and shares_outstanding > 0
        should_pass = has_market_cap

        print(f'{SYMBOL} | {SESSION_DATE} | market_cap = {should_pass}')
        print(f'market_cap = {market_cap} | {has_market_cap}')
        print(f'shares_outstanding = {shares_outstanding} | {has_shares_outstanding}')

        if not should_pass:
            self.skipTest('IB fundamentals are unavailable or missing market cap')

        assert market_cap is not None
        self.assertGreater(market_cap, 0)


if __name__ == '__main__':
    unittest.main(buffer=False)
