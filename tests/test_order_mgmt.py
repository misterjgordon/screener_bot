"""Integration tests for retrieving open (child) orders for a trader and position.

Requires TWS or IB Gateway running with API enabled. Uses real IB connection.
We connect with client_id=0 so we can request all open orders (reqAllOpenOrders),
seeing the screener's orders (client_id=1) without running the screener. Readonly=True is fine.
Uses constants for trader, symbol and direction so you can plug in values and run:
  uv run python -m tests.test_order_mgmt

Use TRADER = '' to see orders placed by the script with no trader (orderRef 'SMB').
Child orders are matched by parent orderId; if the parent has the ref, children are included.
"""

import unittest
import warnings
from typing import TYPE_CHECKING

from trading.market_data import connect
from trading.market_data import disconnect
from trading.trade_data import find_orders_for_symbol_trader

# Suppress before any import that loads ib_async (it uses get_event_loop() with no running loop).
warnings.filterwarnings(
    'ignore',
    category=DeprecationWarning,
    message=r'.*[Nn]o current event loop.*',
)


if TYPE_CHECKING:
    from ib_async import IB

# Test values – change these to inspect a specific position
SYMBOL = 'USO'
TRADER = 'Justin Spero'
IS_LONG = True  # True = long position (open orders are SELL = TP/stop), False = short (open orders are BUY)


def _get_orders_for_direction(ib: 'IB', symbol: str, trader: str, is_long: bool) -> list:
    """Fetch open orders for symbol/trader/position direction. For long, open orders are SELL (TP/stop); for short, BUY."""
    trades = find_orders_for_symbol_trader(ib, symbol, trader)
    want_sell = is_long
    return [t for t in trades if (t.order.action.upper() == 'SELL') == want_sell]


class TestOrderMgmtIntegration(unittest.TestCase):
    """Integration tests: retrieve and print open orders for a trader/symbol/direction."""

    ib: 'IB | None' = None

    @classmethod
    def setUpClass(cls) -> None:
        # Client 0 can call reqAllOpenOrders() to see all clients' orders (screener uses client_id=1).
        cls.ib = connect(readonly=True, client_id=0)
        assert cls.ib is not None and cls.ib.isConnected()

    @classmethod
    def tearDownClass(cls) -> None:
        disconnect(cls.ib)

    def test_retrieve_open_orders_for_trader_position(self) -> None:
        """Retrieve open orders (including child orders) for SYMBOL/TRADER/IS_LONG and print raw data."""
        assert self.ib is not None
        # Debug when we expect orders but get none (shows ref match, by_symbol orderRefs, parentIds).
        raw = find_orders_for_symbol_trader(self.ib, SYMBOL, TRADER, debug=True)
        matching = _get_orders_for_direction(self.ib, SYMBOL, TRADER, IS_LONG)
        direction = 'long' if IS_LONG else 'short'
        print(f'\n{SYMBOL!r} {TRADER!r} {direction}: {len(matching)} order(s)\n')
        self.assertIsInstance(matching, list)


if __name__ == '__main__':
    unittest.main(buffer=False)
