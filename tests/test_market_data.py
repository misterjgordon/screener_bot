"""Integration tests for market_data module.

Requires TWS or IB Gateway running with API enabled. Tests use real IB connection.
Change SYMBOL to test a different stock.

Parity tests compare market_data output to smb_screener output for the same inputs
to ensure behavior is identical before refactoring smb_screener to use market_data.
"""

import unittest

from trading.market_data import (
    calculate_adr as md_calculate_adr,
    calculate_gap_percentage as md_calculate_gap_percentage,
    calculate_trailing_stop as md_calculate_trailing_stop,
    connect,
    disconnect,
    get_market_price as md_get_market_price,
    get_todays_range as md_get_todays_range,
    get_ticker_quote,
)
from trading.smb_screener import (
    calculate_adr as screener_calculate_adr,
    calculate_gap_percentage as screener_calculate_gap_percentage,
    calculate_trailing_stop as screener_calculate_trailing_stop,
    get_market_price as screener_get_market_price,
    get_todays_range as screener_get_todays_range,
)

SYMBOL = 'AAPL'


class TestMarketDataIntegration(unittest.TestCase):
    """Integration tests against real IB API."""

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
        rng = md_get_todays_range(self.ib, SYMBOL)
        if rng is None:
            self.skipTest('No intraday bars (market may be closed)')
        self.assertIsInstance(rng.low, float)
        self.assertIsInstance(rng.high, float)
        self.assertLessEqual(rng.low, rng.high)
        print(f'{SYMBOL} today\'s range: low={rng.low}, high={rng.high}')

    def test_calculate_adr(self) -> None:
        """calculate_adr returns positive float."""
        adr = md_calculate_adr(self.ib, SYMBOL, days=20)
        self.assertIsNotNone(adr)
        assert adr is not None
        self.assertGreater(adr, 0)
        print(f'{SYMBOL} ADR (20d): {adr}')

    def test_calculate_trailing_stop(self) -> None:
        """calculate_trailing_stop returns float during RTH."""
        stop = md_calculate_trailing_stop(self.ib, SYMBOL, prior_bars=3, position_side='long')
        if stop is None:
            self.skipTest('No session bars (market may be closed)')
        self.assertIsInstance(stop, float)
        self.assertGreater(stop, 0)
        print(f'{SYMBOL} trailing stop (long, 3 bars): {stop}')

    def test_calculate_gap_percentage_positive_gap(self) -> None:
        """calculate_gap_percentage returns None or positive for valid input."""
        price = md_get_market_price(self.ib, SYMBOL)
        if price is None:
            self.skipTest('No market price for gap calculation')
        gap = md_calculate_gap_percentage(self.ib, SYMBOL, price)
        if gap is not None:
            self.assertGreater(gap, 0)
        print(f'{SYMBOL} gap %: {gap}')

    def test_get_market_price_unknown_symbol(self) -> None:
        """get_market_price returns None for invalid symbol."""
        price = md_get_market_price(self.ib, 'INVALIDSYMBOLXYZ123')
        self.assertIsNone(price)

    def test_parity_get_market_price(self) -> None:
        """market_data and smb_screener return same get_market_price for same symbol."""
        md_price = md_get_market_price(self.ib, SYMBOL)
        screener_price = screener_get_market_price(self.ib, SYMBOL)
        print(f'{SYMBOL} parity get_market_price: market_data={md_price}, screener={screener_price}')
        if md_price is None and screener_price is None:
            return
        self.assertIsNotNone(md_price)
        self.assertIsNotNone(screener_price)
        assert md_price is not None and screener_price is not None
        self.assertAlmostEqual(md_price, screener_price, places=2)

    def test_parity_get_todays_range(self) -> None:
        """market_data and smb_screener return same get_todays_range for same symbol."""
        md_rng = md_get_todays_range(self.ib, SYMBOL)
        screener_rng = screener_get_todays_range(self.ib, SYMBOL)
        if md_rng is None and screener_rng is None:
            self.skipTest('No intraday bars (market may be closed)')
        md_str = f'({md_rng.low},{md_rng.high})' if md_rng else 'None'
        scr_str = f'({screener_rng.low},{screener_rng.high})' if screener_rng else 'None'
        print(f'{SYMBOL} parity get_todays_range: market_data={md_str}, screener={scr_str}')
        self.assertIsNotNone(md_rng)
        self.assertIsNotNone(screener_rng)
        assert md_rng is not None and screener_rng is not None
        self.assertAlmostEqual(md_rng.low, screener_rng.low, places=2)
        self.assertAlmostEqual(md_rng.high, screener_rng.high, places=2)

    def test_parity_calculate_adr(self) -> None:
        """market_data and smb_screener return same calculate_adr for same symbol."""
        md_adr = md_calculate_adr(self.ib, SYMBOL, days=20)
        screener_adr = screener_calculate_adr(self.ib, SYMBOL, days=20)
        print(f'{SYMBOL} parity calculate_adr: market_data={md_adr}, screener={screener_adr}')
        if md_adr is None and screener_adr is None:
            return
        self.assertIsNotNone(md_adr)
        self.assertIsNotNone(screener_adr)
        assert md_adr is not None and screener_adr is not None
        self.assertAlmostEqual(md_adr, screener_adr, places=4)

    def test_parity_calculate_trailing_stop(self) -> None:
        """market_data and smb_screener return same calculate_trailing_stop for same symbol."""
        md_stop = md_calculate_trailing_stop(self.ib, SYMBOL, prior_bars=3, position_side='long')
        screener_stop = screener_calculate_trailing_stop(self.ib, SYMBOL, prior_bars=3, position_side='long')
        if md_stop is None and screener_stop is None:
            self.skipTest('No session bars (market may be closed)')
        print(f'{SYMBOL} parity trailing_stop: market_data={md_stop}, screener={screener_stop}')
        self.assertIsNotNone(md_stop)
        self.assertIsNotNone(screener_stop)
        assert md_stop is not None and screener_stop is not None
        self.assertAlmostEqual(md_stop, screener_stop, places=2)

    def test_parity_calculate_gap_percentage(self) -> None:
        """market_data and smb_screener return same calculate_gap_percentage for same symbol."""
        price = md_get_market_price(self.ib, SYMBOL)
        self.assertIsNotNone(price)
        assert price is not None
        md_gap = md_calculate_gap_percentage(self.ib, SYMBOL, price)
        screener_gap = screener_calculate_gap_percentage(self.ib, SYMBOL, price)
        print(f'{SYMBOL} parity gap %: market_data={md_gap}, screener={screener_gap}')
        if md_gap is None and screener_gap is None:
            return
        self.assertEqual(md_gap is None, screener_gap is None)
        if md_gap is not None and screener_gap is not None:
            self.assertAlmostEqual(md_gap, screener_gap, places=4)


if __name__ == '__main__':
    unittest.main(buffer=False)
