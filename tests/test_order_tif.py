"""Unit tests: all order placement functions must set tif='GTC'.

Ensures every order leg submitted to IB uses GTC time-in-force, matching the
TWS Presets - Stocks global configuration (TIF = GTC). A mismatch causes TWS
to create a new per-symbol preset on every API order, accumulating presets and
slowing down the system.

No live IB connection required.
Run: uv run --frozen python -m tests.test_order_tif
"""

import unittest
from collections.abc import Callable
from unittest.mock import MagicMock

from trading.ib_trading import send_bracket_order
from trading.ib_trading import send_entry_only_order
from trading.ib_trading import send_market_order
from trading.ib_trading import send_scaling_order

SYMBOL = 'AAPL'
ENTRY_PRICE = 200.0
STOP_PRICE = 195.0
TAKE_PROFIT_PRICE = 210.0
MAGNITUDE = 10.0
NUM_SHARES = 5
TRADER = 'Test Trader'


def make_mock_ib() -> MagicMock:
    """Build a minimal IB mock that captures placeOrder calls and assigns orderId."""
    ib = MagicMock()

    def _place_order(contract: MagicMock, order: MagicMock) -> MagicMock:
        order.orderId = 1001
        mock_trade = MagicMock()
        mock_trade.order = order
        mock_trade.orderStatus.avgFillPrice = 0.0
        return mock_trade

    ib.placeOrder.side_effect = _place_order
    return ib


def _orders_from(fn: Callable[[MagicMock], None]) -> list:
    """Run fn against a fresh mock IB and return all orders passed to placeOrder."""
    ib = make_mock_ib()
    fn(ib)
    return [call.args[1] for call in ib.placeOrder.call_args_list]


class TestOrderTif(unittest.TestCase):
    """All order placement functions must set tif='GTC' on every submitted order."""

    def test_all_order_types_use_gtc(self) -> None:
        """Every order leg from all placement functions uses tif='GTC'.

        Covers bracket (3 legs: entry/TP/stop), entry-only, scaling, and market orders.
        Mismatching TWS preset TIF (DAY vs GTC) causes per-symbol preset accumulation.
        """
        cases: list[tuple[str, list]] = [
            ('bracket', _orders_from(lambda ib: send_bracket_order(ib, SYMBOL, True, ENTRY_PRICE,
             STOP_PRICE, TAKE_PROFIT_PRICE, MAGNITUDE, TRADER, num_shares=NUM_SHARES))),
            ('entry_only', _orders_from(lambda ib: send_entry_only_order(
                ib, SYMBOL, True, ENTRY_PRICE, MAGNITUDE, TRADER, num_shares=NUM_SHARES))),
            ('scaling', _orders_from(lambda ib: send_scaling_order(ib, SYMBOL, True, ENTRY_PRICE, NUM_SHARES, TRADER))),
            ('market', _orders_from(lambda ib: send_market_order(ib, SYMBOL, True, NUM_SHARES, TRADER))),
        ]

        expected_legs = {'bracket': 3, 'entry_only': 1, 'scaling': 1, 'market': 1}

        print(f'{SYMBOL} | order tif check')
        for name, orders in cases:
            self.assertEqual(
                len(orders), expected_legs[name], f'{name}: expected {
                    expected_legs[name]} leg(s), got {
                    len(orders)} — orderId wiring may be broken')
            tifs = [o.tif for o in orders]
            for i, tif in enumerate(tifs):
                leg = ['entry', 'take_profit', 'stop'][i] if name == 'bracket' else 'order'
                print(f'{name} {leg} tif = {tif!r} | {tif == "GTC"}')
            self.assertTrue(all(t == 'GTC' for t in tifs), f'{name}: expected all GTC, got {tifs}')


if __name__ == '__main__':
    unittest.main(buffer=False)
