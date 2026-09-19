"""Unit tests for decision-time price capture in TRIM/CLOSE/FLIP."""

import unittest
from unittest.mock import patch

from trading.models import PositionSummary
from trading.trade_mgmt import process_close
from trading.trade_mgmt import process_flip
from trading.trade_mgmt import process_trim


def _make_row(*, change_type: str, net_side: str, delta_magnitude: float) -> PositionSummary:
    return PositionSummary(
        trader='Justin Spero',
        is_long_term=False,
        symbol='AAPL',
        instrument_type='equity',
        underlying='AAPL',
        expiry=None,
        strike=None,
        option_type=None,
        net_side=net_side,
        conflict=False,
        total_magnitude=0.0,
        prev_magnitude=0.0,
        delta_magnitude=delta_magnitude,
        change_type=change_type,
    )


class TestTradeMgmtDecisionPrice(unittest.TestCase):
    def test_trim_records_entry_price_without_order(self) -> None:
        row = _make_row(change_type='TRIM', net_side='long', delta_magnitude=-25.0)
        with (
            patch('trading.trade_mgmt.get_decision_price_for_recording', return_value=10.13),
            patch('trading.trade_mgmt.ACTIVE_TRADING', False),
            patch('trading.trade_mgmt.save_execution_to_csv') as save_csv,
            patch('trading.trade_mgmt.save_execution_to_db') as save_db,
        ):
            process_trim(None, row, 'AAPL', shares_override=None)

        self.assertEqual(save_csv.call_args.kwargs['entry_price'], 10.13)
        self.assertEqual(save_db.call_args.kwargs['entry_price'], 10.13)
        self.assertIsNone(save_csv.call_args.kwargs['filled_price'])
        self.assertIsNone(save_db.call_args.kwargs['filled_price'])

    def test_close_records_entry_price_without_order(self) -> None:
        row = _make_row(change_type='CLOSE', net_side='flat', delta_magnitude=-10.0)
        with (
            patch('trading.trade_mgmt.get_decision_price_for_recording', return_value=20.99),
            patch('trading.trade_mgmt.ACTIVE_TRADING', False),
            patch('trading.trade_mgmt.save_execution_to_csv') as save_csv,
            patch('trading.trade_mgmt.save_execution_to_db') as save_db,
        ):
            process_close(None, row, 'AAPL', shares_override=None)

        self.assertEqual(save_csv.call_args.kwargs['entry_price'], 20.99)
        self.assertEqual(save_db.call_args.kwargs['entry_price'], 20.99)
        self.assertIsNone(save_csv.call_args.kwargs['filled_price'])
        self.assertIsNone(save_db.call_args.kwargs['filled_price'])

    def test_flip_records_entry_price_without_order(self) -> None:
        """FLIP's own audit record (first save) has no filled_price key.

        process_flip also flattens (via process_close) and, with ib=None, tries to
        reopen too - both no-op without an order since IB is unavailable - so only
        the first save call (the FLIP audit record itself) is checked here.
        """
        row = _make_row(change_type='FLIP', net_side='short', delta_magnitude=-20.0)
        with (
            patch('trading.trade_mgmt.get_decision_price_for_recording', return_value=31.0),
            patch('trading.trade_mgmt.save_execution_to_csv') as save_csv,
            patch('trading.trade_mgmt.save_execution_to_db') as save_db,
        ):
            process_flip(None, row, 'AAPL')

        flip_csv_call = save_csv.call_args_list[0]
        flip_db_call = save_db.call_args_list[0]
        self.assertEqual(flip_csv_call.kwargs['change_type'], 'FLIP')
        self.assertEqual(flip_csv_call.kwargs['entry_price'], 31.0)
        self.assertEqual(flip_db_call.kwargs['entry_price'], 31.0)
        self.assertNotIn('filled_price', flip_csv_call.kwargs)
        self.assertNotIn('filled_price', flip_db_call.kwargs)


if __name__ == '__main__':
    unittest.main()
