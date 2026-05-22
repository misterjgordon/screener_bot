"""Closed trade records produced by the portfolio simulator (not bar-table columns)."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

ExitReason = Literal['stop_loss', 'take_profit', 'end_of_session']


@dataclass(frozen=True)
class Trade:
    """One round-trip position from entry fill through exit fill."""

    symbol: str
    trading_date: date
    entry_timestamp_utc: object
    entry_price: float
    exit_timestamp_utc: object
    exit_price: float
    exit_reason: ExitReason
    shares: float
    pnl: float
    pnl_pct: float
