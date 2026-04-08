"""Integration tests for market_data module.

Requires TWS or IB Gateway running with API enabled. Tests use real IB connection.
Uses last_trading_day so tests run on weekends (defaults to Friday).
Change SYMBOL to test a different stock.
"""

import unittest

from strategies.indicators.adr import calculate_adr as md_calculate_adr
from strategies.utils import last_trading_day
from trading.bar_loader import load_bars
from trading.market_data import calculate_gap_percentage as md_calculate_gap_percentage
from trading.market_data import calculate_trailing_stop as md_calculate_trailing_stop
from trading.market_data import connect
from trading.market_data import disconnect
from trading.market_data import get_market_price as md_get_market_price
from trading.market_data import get_ticker_quote
from trading.market_data import get_todays_range as md_get_todays_range

SYMBOL = 'AAPL'


class TestMarketDataIntegration(unittest.TestCase):
    """Integration tests against real IB API."""

    ib = None
    bundle = None

    @classmethod
    def setUpClass(cls) -> None:
        """Connect once and load bars once for all tests."""
        cls.ib = connect(readonly=True)
        assert cls.ib is not None and cls.ib.isConnected()
        cls.bundle = load_bars(cls.ib, SYMBOL)

    @classmethod
    def tearDownClass(cls) -> None:
        """Disconnect after all tests."""
        disconnect(cls.ib)

    def test_get_market_price(self) -> None:
        """get_market_price returns positive float."""
        price = md_get_market_price(self.ib, SYMBOL)
        self.assertIsNotNone(price)
        assert price is not None
        self.assertIsInstance(price, float)
        self.assertGreater(price, 0)
        print(f'{SYMBOL} market price: {price}')

    def test_get_ticker_quote(self) -> None:
        """get_ticker_quote returns TickerQuote with at least one price."""
        quote = get_ticker_quote(self.ib, SYMBOL)
        self.assertIsNotNone(quote)
        assert quote is not None
        best = quote.best_price()
        self.assertIsNotNone(best)
        assert best is not None
        self.assertGreater(best, 0)

    def test_get_todays_range(self) -> None:
        """get_todays_range returns DayRange with low <= high during RTH."""
        session_date = last_trading_day()
        rng = md_get_todays_range(self.ib, SYMBOL, bundle=self.bundle)
        if rng is None:
            self.skipTest('No intraday bars (market may be closed)')
        assert rng is not None
        self.assertIsInstance(rng.low, float)
        self.assertIsInstance(rng.high, float)
        self.assertLessEqual(rng.low, rng.high)
        print(f'{SYMBOL} range ({session_date}): low={rng.low}, high={rng.high}')

    def test_calculate_adr(self) -> None:
        """calculate_adr returns positive float."""
        adr = md_calculate_adr(self.ib, SYMBOL, days=20, bundle=self.bundle)
        self.assertIsNotNone(adr)
        assert adr is not None
        self.assertGreater(adr, 0)
        print(f'{SYMBOL} ADR (20d): {adr}')

    def test_calculate_trailing_stop(self) -> None:
        """calculate_trailing_stop returns float during RTH."""
        session_date = last_trading_day()
        stop = md_calculate_trailing_stop(
            self.ib, SYMBOL, prior_bars=3, position_side='long', bundle=self.bundle
        )
        if stop is None:
            self.skipTest('No session bars (market may be closed)')
        assert stop is not None
        self.assertIsInstance(stop, float)
        self.assertGreater(stop, 0)
        print(f'{SYMBOL} trailing stop (long, 3 bars, {session_date}): {stop}')

    def test_calculate_gap_percentage_positive_gap(self) -> None:
        """calculate_gap_percentage returns None or positive for valid input."""
        price = md_get_market_price(self.ib, SYMBOL)
        if price is None:
            self.skipTest('No market price for gap calculation')
        assert price is not None
        gap = md_calculate_gap_percentage(
            self.ib, SYMBOL, price, bundle=self.bundle
        )
        if gap is not None:
            self.assertGreater(gap, 0)
        print(f'{SYMBOL} gap %: {gap}')

    def test_get_market_price_unknown_symbol(self) -> None:
        """get_market_price returns None for invalid symbol."""
        price = md_get_market_price(self.ib, 'INVALIDSYMBOLXYZ123')
        self.assertIsNone(price)


if __name__ == '__main__':
    unittest.main(buffer=False)
