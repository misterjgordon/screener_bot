"""Integration tests for trade_mgmt module.

Requires TWS or IB Gateway running with API enabled on live account (7496).
Tests use real IB connection; orders are sent when ACTIVE_TRADING is True.
Uses own IB connection (client ID 4); runs independently of asyncio/screener.
Uses constants below (mirroring PositionSummary) to configure inputs.
SHARES_OVERRIDE: when set, use this share count instead of calculated. None = normal calculation.
Run: python -m tests.test_trade_mgmt
"""

import unittest
from typing import TYPE_CHECKING

from trading.config import IB_CLIENT_ID_TRADE_MGMT
from trading.market_data import connect
from trading.market_data import disconnect
from trading.models import PositionSummary
from trading.trade_data import get_position_size
from trading.trade_mgmt import process_execution_change

if TYPE_CHECKING:
    from ib_async import IB

# PositionSummary constants (adjust for your test inputs)
TRADER = 'Justin Spero'
IS_LONG_TERM = False
SYMBOL = 'AAPL'
INSTRUMENT_TYPE = 'equity'
UNDERLYING = 'AAPL'
EXPIRY = None
STRIKE = None
OPTION_TYPE = None
NET_SIDE = 'long'  # 'long' | 'short' | 'flat' | 'conflict'
CONFLICT = False
TOTAL_MAGNITUDE = 10.0
PREV_MAGNITUDE: float | None = 0.0
DELTA_MAGNITUDE: float | None = 10.0
CHANGE_TYPE = 'NEW'  # 'NEW' | 'ADD' | 'TRIM' | 'CLOSE' | 'FLIP'

# Override share count; None = use normal calculation
SHARES_OVERRIDE: int | None = None


def make_position_summary(
    trader: str = TRADER,
    is_long_term: bool = IS_LONG_TERM,
    symbol: str = SYMBOL,
    instrument_type: str = INSTRUMENT_TYPE,
    underlying: str | None = None,
    expiry: str | None = EXPIRY,
    strike: float | None = STRIKE,
    option_type: str | None = OPTION_TYPE,
    net_side: str = NET_SIDE,
    conflict: bool = CONFLICT,
    total_magnitude: float = TOTAL_MAGNITUDE,
    prev_magnitude: float | None = PREV_MAGNITUDE,
    delta_magnitude: float | None = DELTA_MAGNITUDE,
    change_type: str | None = CHANGE_TYPE,
) -> PositionSummary:
    """Build PositionSummary from constants; override fields via keyword args."""
    return PositionSummary(
        trader=trader,
        is_long_term=is_long_term,
        symbol=symbol,
        instrument_type=instrument_type or 'equity',
        underlying=underlying or symbol,
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        net_side=net_side,
        conflict=conflict,
        total_magnitude=total_magnitude,
        prev_magnitude=prev_magnitude,
        delta_magnitude=delta_magnitude,
        change_type=change_type,
    )


class TestTradeMgmtIntegration(unittest.TestCase):
    """Integration tests for process_execution_change against live IB."""

    ib: 'IB | None' = None

    @classmethod
    def setUpClass(cls) -> None:
        """Connect once for all tests (live account, readonly=False for order execution)."""
        cls.ib = connect(client_id=IB_CLIENT_ID_TRADE_MGMT, readonly=False)
        assert cls.ib is not None and cls.ib.isConnected()

    @classmethod
    def tearDownClass(cls) -> None:
        """Disconnect after all tests."""
        disconnect(cls.ib)

    def _run_process_execution_change(
        self,
        change_type: str,
        net_side: str = NET_SIDE,
        delta_magnitude: float | None = DELTA_MAGNITUDE,
        prev_magnitude: float | None = PREV_MAGNITUDE,
        total_magnitude: float = TOTAL_MAGNITUDE,
        shares_override: int | None = SHARES_OVERRIDE,
    ) -> None:
        """Build PositionSummary and call process_execution_change."""
        row = make_position_summary(
            net_side=net_side,
            total_magnitude=total_magnitude,
            prev_magnitude=prev_magnitude,
            delta_magnitude=delta_magnitude,
            change_type=change_type,
        )
        process_execution_change(self.ib, row, change_type, shares_override=shares_override)

    def test_new(self) -> None:
        """NEW: open a new position (requires no existing position)."""
        self._run_process_execution_change(
            change_type='NEW',
            net_side='long',
            delta_magnitude=10.0,
            prev_magnitude=0.0,
            total_magnitude=10.0,
            shares_override=SHARES_OVERRIDE,
        )

    def test_add(self) -> None:
        """ADD: add to existing position (requires existing position in same direction)."""
        self._run_process_execution_change(
            change_type='ADD',
            net_side='long',
            delta_magnitude=5.0,
            prev_magnitude=10.0,
            total_magnitude=15.0,
            shares_override=SHARES_OVERRIDE,
        )

    def test_trim(self) -> None:
        """TRIM: reduce position (requires existing position)."""
        assert self.ib is not None
        current_position = get_position_size(self.ib, SYMBOL)
        if current_position == 0:
            self.skipTest(f'No position in {SYMBOL} - cannot test TRIM')

        self._run_process_execution_change(
            change_type='TRIM',
            net_side='long',
            delta_magnitude=-25.0,  # trim 25% of position
            prev_magnitude=10.0,
            total_magnitude=7.5,
            shares_override=SHARES_OVERRIDE,
        )

    def test_close(self) -> None:
        """CLOSE: exit entire position."""
        assert self.ib is not None
        current_position = get_position_size(self.ib, SYMBOL)
        if current_position == 0:
            self.skipTest(f'No position in {SYMBOL} - cannot test CLOSE')

        self._run_process_execution_change(
            change_type='CLOSE',
            net_side='flat',
            delta_magnitude=-10.0,
            prev_magnitude=10.0,
            total_magnitude=0.0,
            shares_override=SHARES_OVERRIDE,
        )

    def test_flip(self) -> None:
        """FLIP: tracks only (no order sent)."""
        self._run_process_execution_change(
            change_type='FLIP',
            net_side='short',
            delta_magnitude=-20.0,
            prev_magnitude=10.0,
            total_magnitude=-10.0,
        )


if __name__ == '__main__':
    unittest.main(buffer=False)
