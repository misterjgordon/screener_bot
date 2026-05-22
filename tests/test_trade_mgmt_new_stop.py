"""Unit tests for NEW-order stop requirements and stop/TP helpers in trade_mgmt."""

import unittest
from unittest.mock import MagicMock
from unittest.mock import patch

from trading.ib_trading import OrderEntry
from trading.models import PositionSummary
from trading.trade_mgmt import EntryStopTakeProfit
from trading.trade_mgmt import is_usable_stop
from trading.trade_mgmt import place_add_order
from trading.trade_mgmt import place_new_order
from trading.trade_mgmt import resolve_stop_price
from trading.trade_mgmt import risk_per_share_at_stop
from trading.trade_mgmt import take_profit_fallback_from_stop
from trading.trade_mgmt import take_profit_from_adr


class TestStopHelpers(unittest.TestCase):
    """Pure helpers for stop distance and take-profit."""

    def test_risk_per_share_long(self) -> None:
        risk = risk_per_share_at_stop(100.0, 95.0, is_long=True)
        self.assertEqual(risk, 5.0)

    def test_risk_per_share_invalid_long(self) -> None:
        self.assertIsNone(risk_per_share_at_stop(100.0, 101.0, is_long=True))

    def test_is_usable_stop(self) -> None:
        self.assertTrue(is_usable_stop(100.0, 95.0, is_long=True))
        self.assertFalse(is_usable_stop(100.0, None, is_long=True))
        self.assertFalse(is_usable_stop(100.0, 101.0, is_long=True))

    def test_take_profit_from_adr_long(self) -> None:
        self.assertEqual(take_profit_from_adr(100.0, 10.0, is_long=True), 106.0)

    def test_take_profit_fallback_from_stop_long(self) -> None:
        # stop risk 5 → TP +6 (1.2x)
        self.assertEqual(take_profit_fallback_from_stop(100.0, 95.0, is_long=True), 106.0)


class TestPlaceNewOrderStopRule(unittest.TestCase):
    """NEW must not use entry-only; requires usable stop (+ TP for bracket)."""

    def _market(
        self,
        *,
        stop: float | None,
        take_profit: float | None = 110.0,
    ) -> EntryStopTakeProfit:
        return EntryStopTakeProfit(
            entry_price=100.0,
            stop_price=stop,
            take_profit_price=take_profit,
            adjusted_magnitude=54.0,
            bundle=None,
        )

    def _row(self) -> PositionSummary:
        return PositionSummary(
            trader='Justin Spero',
            is_long_term=False,
            symbol='NVDA',
            instrument_type='equity',
            underlying='NVDA',
            expiry=None,
            strike=None,
            option_type=None,
            net_side='long',
            conflict=False,
            total_magnitude=54.0,
            prev_magnitude=0.0,
            delta_magnitude=54.0,
            change_type='NEW',
        )

    @patch('trading.trade_mgmt.send_entry_only_order')
    @patch('trading.trade_mgmt.send_bracket_order')
    @patch('trading.trade_mgmt.get_entry_mode')
    @patch('trading.trade_mgmt.has_open_orders_for_trader', return_value=False)
    @patch('trading.trade_mgmt.get_position_size', return_value=0)
    def test_new_skips_without_stop(
        self,
        _pos: MagicMock,
        _open: MagicMock,
        entry_mode: MagicMock,
        bracket: MagicMock,
        entry_only: MagicMock,
    ) -> None:
        entry_mode.return_value = MagicMock(skip=False, entry_price=100.0, order_type='limit')
        ib = MagicMock()

        result = place_new_order(
            ib,
            self._row(),
            'NVDA',
            is_long=True,
            market=self._market(
                stop=None),
            shares_override=None)

        self.assertEqual(result.no_place_reason, 'new_requires_stop')
        bracket.assert_not_called()
        entry_only.assert_not_called()

    @patch('trading.trade_mgmt.send_entry_only_order')
    @patch('trading.trade_mgmt.send_bracket_order')
    @patch('trading.trade_mgmt.get_entry_mode')
    @patch('trading.trade_mgmt.has_open_orders_for_trader', return_value=False)
    @patch('trading.trade_mgmt.get_position_size', return_value=0)
    def test_new_uses_bracket_with_stop(
        self,
        _pos: MagicMock,
        _open: MagicMock,
        entry_mode: MagicMock,
        bracket: MagicMock,
        entry_only: MagicMock,
    ) -> None:
        entry_mode.return_value = MagicMock(skip=False, entry_price=100.0, order_type='limit')
        bracket.return_value = OrderEntry('1', 100.0, 10, 25.0, 2.5)
        ib = MagicMock()

        result = place_new_order(
            ib,
            self._row(),
            'NVDA',
            is_long=True,
            market=self._market(stop=95.0, take_profit=106.0),
            shares_override=None,
        )

        self.assertIsNone(result.no_place_reason)
        bracket.assert_called_once()
        entry_only.assert_not_called()


class TestPlaceAddOrderEntryOnly(unittest.TestCase):
    """ADD without position may still use entry-only when stop/TP missing."""

    @patch('trading.trade_mgmt.send_entry_only_order')
    @patch('trading.trade_mgmt.send_bracket_order')
    @patch('trading.trade_mgmt.get_entry_mode')
    @patch('trading.trade_mgmt.has_open_orders', return_value=False)
    @patch('trading.trade_mgmt.get_position_size', return_value=0)
    def test_add_no_position_allows_entry_only(
        self,
        _pos: MagicMock,
        _open: MagicMock,
        entry_mode: MagicMock,
        bracket: MagicMock,
        entry_only: MagicMock,
    ) -> None:
        entry_mode.return_value = MagicMock(skip=False, entry_price=100.0, order_type='limit')
        entry_only.return_value = OrderEntry('2', 100.0, 5, 1.5, 0.3)
        row = PositionSummary(
            trader='Justin Spero',
            is_long_term=False,
            symbol='NVDA',
            instrument_type='equity',
            underlying='NVDA',
            expiry=None,
            strike=None,
            option_type=None,
            net_side='long',
            conflict=False,
            total_magnitude=60.0,
            prev_magnitude=54.0,
            delta_magnitude=6.0,
            change_type='ADD',
        )
        market = EntryStopTakeProfit(
            entry_price=100.0,
            stop_price=None,
            take_profit_price=None,
            adjusted_magnitude=6.0,
            bundle=None,
        )
        ib = MagicMock()

        place_add_order(ib, row, 'NVDA', is_long=True, market=market, delta_magnitude=6.0, shares_override=None)

        entry_only.assert_called_once()
        bracket.assert_not_called()


class TestResolveStopPriceWithoutAdr(unittest.TestCase):
    """Trailing/day-range stops do not require ADR."""

    @patch('trading.trade_mgmt.get_todays_range', return_value=None)
    @patch('trading.trade_mgmt.calculate_trailing_stop', return_value=99.0)
    def test_trailing_stop_without_adr(
        self,
        trailing: MagicMock,
        day_range: MagicMock,
    ) -> None:
        ib = MagicMock()
        stop = resolve_stop_price(ib, 'NVDA', is_long=True, entry_price=100.0, bundle=None, adr=None)
        trailing.assert_called_once()
        day_range.assert_not_called()
        self.assertEqual(stop, 98.98)  # 99.0 - STOP_OFFSET


if __name__ == '__main__':
    unittest.main(buffer=False)
