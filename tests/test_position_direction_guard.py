"""Unit tests for the bot-vs-trader position direction guard and fail-guard.

Covers the fix for the bot ending up net-opposite the trader: ADD now blocks
when IB already holds the opposite side, FLIP flattens before reopening, and
enforce_position_direction_guard is a per-cycle backstop that flattens any
residual inverse position regardless of change_type.
"""

import unittest
from unittest.mock import MagicMock
from unittest.mock import patch

from trading.models import PositionSummary
from trading.trade_mgmt import EntryStopTakeProfit
from trading.trade_mgmt import enforce_position_direction_guard
from trading.trade_mgmt import place_add_order
from trading.trade_mgmt import process_flip


def _row(
    *,
    net_side: str = 'long',
    trader: str = 'Justin Spero',
    symbol: str = 'NVDA',
    delta_magnitude: float = 10.0,
) -> PositionSummary:
    return PositionSummary(
        trader=trader,
        is_long_term=False,
        symbol=symbol,
        instrument_type='equity',
        underlying=symbol,
        expiry=None,
        strike=None,
        option_type=None,
        net_side=net_side,
        conflict=False,
        total_magnitude=10.0,
        prev_magnitude=0.0,
        delta_magnitude=delta_magnitude,
        change_type='ADD',
    )


class TestPlaceAddOrderDirectionGuard(unittest.TestCase):
    """ADD must refuse to add on top of an opposite-side IB position."""

    @patch('trading.trade_mgmt.send_scaling_order')
    @patch('trading.trade_mgmt.send_entry_only_order')
    @patch('trading.trade_mgmt.send_bracket_order')
    @patch('trading.trade_mgmt.get_position_size', return_value=-50)
    def test_add_blocked_when_ib_position_is_short_but_signal_is_long(
        self,
        _pos: MagicMock,
        bracket: MagicMock,
        entry_only: MagicMock,
        scaling: MagicMock,
    ) -> None:
        market = EntryStopTakeProfit(
            entry_price=100.0,
            stop_price=95.0,
            take_profit_price=106.0,
            adjusted_magnitude=10.0,
            bundle=None,
        )
        ib = MagicMock()

        result = place_add_order(
            ib, _row(net_side='long'), 'NVDA', is_long=True, market=market, delta_magnitude=10.0, shares_override=None
        )

        self.assertIsNotNone(result.no_place_reason)
        assert result.no_place_reason is not None
        self.assertIn('position direction mismatch', result.no_place_reason)
        bracket.assert_not_called()
        entry_only.assert_not_called()
        scaling.assert_not_called()

    @patch('trading.trade_mgmt.send_scaling_order')
    @patch('trading.trade_mgmt.send_entry_only_order')
    @patch('trading.trade_mgmt.send_bracket_order')
    @patch('trading.trade_mgmt.get_position_size', return_value=50)
    def test_add_blocked_when_ib_position_is_long_but_signal_is_short(
        self,
        _pos: MagicMock,
        bracket: MagicMock,
        entry_only: MagicMock,
        scaling: MagicMock,
    ) -> None:
        market = EntryStopTakeProfit(
            entry_price=100.0,
            stop_price=105.0,
            take_profit_price=94.0,
            adjusted_magnitude=10.0,
            bundle=None,
        )
        ib = MagicMock()

        result = place_add_order(
            ib, _row(net_side='short'), 'NVDA', is_long=False, market=market, delta_magnitude=10.0, shares_override=None
        )

        self.assertIsNotNone(result.no_place_reason)
        assert result.no_place_reason is not None
        self.assertIn('position direction mismatch', result.no_place_reason)
        bracket.assert_not_called()
        entry_only.assert_not_called()
        scaling.assert_not_called()


class TestProcessFlip(unittest.TestCase):
    """FLIP must flatten the old side before opening the new one, never overlap them."""

    @patch('trading.trade_mgmt.save_execution_to_db')
    @patch('trading.trade_mgmt.save_execution_to_csv')
    @patch('trading.trade_mgmt.get_decision_price_for_recording', return_value=100.0)
    @patch('trading.trade_mgmt.process_new_or_add')
    @patch('trading.trade_mgmt.wait_for_flat_position', return_value=True)
    @patch('trading.trade_mgmt.process_close')
    @patch('trading.trade_mgmt.ACTIVE_TRADING', True)
    def test_flip_closes_then_reopens_when_flat_confirmed(
        self,
        close: MagicMock,
        wait_flat: MagicMock,
        new_or_add: MagicMock,
        _price: MagicMock,
        _csv: MagicMock,
        _db: MagicMock,
    ) -> None:
        ib = MagicMock()
        ib.isConnected.return_value = True
        row = _row(net_side='short')

        process_flip(ib, row, 'NVDA', shares_override=None)

        close.assert_called_once_with(ib, row, 'NVDA', shares_override=None)
        wait_flat.assert_called_once_with(ib, 'NVDA')
        new_or_add.assert_called_once_with(ib, row, 'NEW', None)

    @patch('trading.trade_mgmt.save_execution_to_db')
    @patch('trading.trade_mgmt.save_execution_to_csv')
    @patch('trading.trade_mgmt.get_decision_price_for_recording', return_value=100.0)
    @patch('trading.trade_mgmt.process_new_or_add')
    @patch('trading.trade_mgmt.wait_for_flat_position', return_value=False)
    @patch('trading.trade_mgmt.process_close')
    @patch('trading.trade_mgmt.ACTIVE_TRADING', True)
    def test_flip_does_not_reopen_when_not_confirmed_flat(
        self,
        close: MagicMock,
        wait_flat: MagicMock,
        new_or_add: MagicMock,
        _price: MagicMock,
        _csv: MagicMock,
        _db: MagicMock,
    ) -> None:
        ib = MagicMock()
        ib.isConnected.return_value = True
        row = _row(net_side='short')

        process_flip(ib, row, 'NVDA', shares_override=None)

        close.assert_called_once()
        new_or_add.assert_not_called()

    @patch('trading.trade_mgmt.save_execution_to_db')
    @patch('trading.trade_mgmt.save_execution_to_csv')
    @patch('trading.trade_mgmt.get_decision_price_for_recording', return_value=100.0)
    @patch('trading.trade_mgmt.process_new_or_add')
    @patch('trading.trade_mgmt.wait_for_flat_position')
    @patch('trading.trade_mgmt.process_close')
    def test_flip_conflict_side_flattens_only(
        self,
        close: MagicMock,
        wait_flat: MagicMock,
        new_or_add: MagicMock,
        _price: MagicMock,
        _csv: MagicMock,
        _db: MagicMock,
    ) -> None:
        ib = MagicMock()
        ib.isConnected.return_value = True
        row = _row(net_side='conflict')

        process_flip(ib, row, 'NVDA', shares_override=None)

        close.assert_called_once()
        wait_flat.assert_not_called()
        new_or_add.assert_not_called()

    @patch('trading.trade_mgmt.save_execution_to_db')
    @patch('trading.trade_mgmt.save_execution_to_csv')
    @patch('trading.trade_mgmt.get_decision_price_for_recording', return_value=100.0)
    @patch('trading.trade_mgmt.process_new_or_add')
    @patch('trading.trade_mgmt.wait_for_flat_position')
    @patch('trading.trade_mgmt.process_close')
    @patch('trading.trade_mgmt.ACTIVE_TRADING', False)
    def test_flip_does_not_reopen_when_active_trading_disabled(
        self,
        close: MagicMock,
        wait_flat: MagicMock,
        new_or_add: MagicMock,
        _price: MagicMock,
        _csv: MagicMock,
        _db: MagicMock,
    ) -> None:
        ib = MagicMock()
        ib.isConnected.return_value = True
        row = _row(net_side='short')

        process_flip(ib, row, 'NVDA', shares_override=None)

        close.assert_called_once()
        wait_flat.assert_not_called()
        new_or_add.assert_not_called()


class TestEnforcePositionDirectionGuard(unittest.TestCase):
    """Per-cycle backstop: flatten any bot position opposite the trader's side."""

    @patch('trading.trade_mgmt.process_close')
    @patch('trading.trade_mgmt.get_position_size', return_value=-10)
    @patch('trading.trade_mgmt.TRADER_ENABLED', {'Justin Spero': True})
    @patch('trading.trade_mgmt.ACTIVE_TRADING', True)
    def test_guard_flattens_inverse_position(self, _pos: MagicMock, close: MagicMock) -> None:
        ib = MagicMock()
        ib.isConnected.return_value = True
        row = _row(net_side='long')

        enforce_position_direction_guard(ib, [row])

        close.assert_called_once_with(ib, row, 'NVDA', shares_override=None)

    @patch('trading.trade_mgmt.process_close')
    @patch('trading.trade_mgmt.get_position_size', return_value=10)
    @patch('trading.trade_mgmt.TRADER_ENABLED', {'Justin Spero': True})
    @patch('trading.trade_mgmt.ACTIVE_TRADING', True)
    def test_guard_skips_matching_position(self, _pos: MagicMock, close: MagicMock) -> None:
        ib = MagicMock()
        ib.isConnected.return_value = True
        row = _row(net_side='long')

        enforce_position_direction_guard(ib, [row])

        close.assert_not_called()

    @patch('trading.trade_mgmt.process_close')
    @patch('trading.trade_mgmt.get_position_size', return_value=0)
    @patch('trading.trade_mgmt.TRADER_ENABLED', {'Justin Spero': True})
    @patch('trading.trade_mgmt.ACTIVE_TRADING', True)
    def test_guard_skips_flat_position(self, _pos: MagicMock, close: MagicMock) -> None:
        ib = MagicMock()
        ib.isConnected.return_value = True
        row = _row(net_side='long')

        enforce_position_direction_guard(ib, [row])

        close.assert_not_called()

    @patch('trading.trade_mgmt.process_close')
    @patch('trading.trade_mgmt.get_position_size', return_value=-10)
    @patch('trading.trade_mgmt.TRADER_ENABLED', {'Justin Spero': True})
    @patch('trading.trade_mgmt.ACTIVE_TRADING', False)
    def test_guard_does_not_flatten_when_active_trading_disabled(self, _pos: MagicMock, close: MagicMock) -> None:
        ib = MagicMock()
        ib.isConnected.return_value = True
        row = _row(net_side='long')

        enforce_position_direction_guard(ib, [row])

        close.assert_not_called()


if __name__ == '__main__':
    unittest.main()
