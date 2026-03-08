"""Integration tests for trade_data module.

Requires TWS or IB Gateway running with API enabled. Tests use real IB connection.
Uses last_trading_day from strategies.utils so tests run on weekends (defaults to Friday).
Uses constant SYMBOL (AAPL) and TRADER (Justin Spero). Run independently;
does not modify production code or state.
shell function:
python -m tests.test_trade_data
"""

import unittest
from typing import TYPE_CHECKING

from strategies.utils import last_trading_day
from trading.market_data import connect, disconnect

if TYPE_CHECKING:
    from ib_async import IB
from trading.trade_data import (
    find_orders_for_symbol_trader,
    get_available_funds,
    get_position_size,
    has_open_orders,
    has_open_orders_for_trader,
    order_tag,
)

# =========================================================
# Test values
# =========================================================
SYMBOL = 'IBIT'
TRADER = 'Justin Spero'


class TestTradeDataIntegration(unittest.TestCase):
    """Integration tests against real IB API."""

    ib: 'IB | None' = None

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

    def test_order_tag(self) -> None:
        """order_tag returns expected string format."""
        ref = order_tag(TRADER)
        self.assertIsInstance(ref, str)
        self.assertIn(TRADER, ref)
        print(f'order_tag({TRADER!r}): {ref!r}')

    def test_get_available_funds(self) -> None:
        """get_available_funds returns non-negative float."""
        assert self.ib is not None
        session_date = last_trading_day()
        funds = get_available_funds(self.ib)
        self.assertIsInstance(funds, float)
        self.assertGreaterEqual(funds, 0.0)
        print(f'get_available_funds() ({session_date}): ${funds:,.2f}')

    def test_get_position_size(self) -> None:
        """get_position_size returns int (pos/neg/zero)."""
        assert self.ib is not None
        size = get_position_size(self.ib, SYMBOL)
        self.assertIsInstance(size, int)
        if size != 0:
            print(f'get_position_size({SYMBOL!r}): {size} ({"LONG" if size > 0 else "SHORT"})')
        else:
            print(f'get_position_size({SYMBOL!r}): {size} (flat)')

    def test_has_open_orders_any(self) -> None:
        """has_open_orders returns bool for any direction."""
        assert self.ib is not None
        has_any = has_open_orders(self.ib, SYMBOL, is_long=None)
        self.assertIsInstance(has_any, bool)
        print(f'has_open_orders({SYMBOL!r}, is_long=None): {has_any}')

    def test_has_open_orders_long(self) -> None:
        """has_open_orders returns bool for long only."""
        assert self.ib is not None
        has_long = has_open_orders(self.ib, SYMBOL, is_long=True)
        self.assertIsInstance(has_long, bool)
        print(f'has_open_orders({SYMBOL!r}, is_long=True): {has_long}')

    def test_has_open_orders_short(self) -> None:
        """has_open_orders returns bool for short only."""
        assert self.ib is not None
        has_short = has_open_orders(self.ib, SYMBOL, is_long=False)
        self.assertIsInstance(has_short, bool)
        print(f'has_open_orders({SYMBOL!r}, is_long=False): {has_short}')

    def test_has_open_orders_for_trader_long(self) -> None:
        """has_open_orders_for_trader returns bool for long."""
        assert self.ib is not None
        has_it = has_open_orders_for_trader(self.ib, SYMBOL, is_long=True, trader=TRADER)
        self.assertIsInstance(has_it, bool)
        print(f'has_open_orders_for_trader({SYMBOL!r}, is_long=True, trader={TRADER!r}): {has_it}')

    def test_has_open_orders_for_trader_short(self) -> None:
        """has_open_orders_for_trader returns bool for short."""
        assert self.ib is not None
        has_it = has_open_orders_for_trader(self.ib, SYMBOL, is_long=False, trader=TRADER)
        self.assertIsInstance(has_it, bool)
        print(f'has_open_orders_for_trader({SYMBOL!r}, is_long=False, trader={TRADER!r}): {has_it}')

    def test_find_orders_for_symbol_trader(self) -> None:
        """find_orders_for_symbol_trader returns list of matching trades."""
        assert self.ib is not None
        trades = find_orders_for_symbol_trader(self.ib, SYMBOL, trader=TRADER)
        self.assertIsInstance(trades, list)
        print(f'find_orders_for_symbol_trader({SYMBOL!r}, trader={TRADER!r}): {len(trades)} order(s)')


if __name__ == '__main__':
    unittest.main(buffer=False)
