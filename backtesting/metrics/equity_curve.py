"""Daily equity curve from closed trades."""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from backtesting.portfolio.trade import Trade


@dataclass(frozen=True)
class EquityPoint:
    """Portfolio equity at the close of one business day."""

    trading_date: date
    equity: float
    daily_pnl: float
    daily_return: float  # fraction; 0.0 on days with no trades


def build_equity_curve(
    trades: tuple[Trade, ...],
    *,
    initial_capital: float,
    start: date,
    end: date,
) -> tuple[EquityPoint, ...]:
    """Daily equity curve over business days in [start, end].

    Equity on each date = prior equity + sum of PnL for trades whose trading_date matches.
    Uses Mon-Fri business day approximation; US market holidays appear as flat days.
    """
    if initial_capital <= 0:
        msg = f'initial_capital must be > 0, got {initial_capital}'
        raise ValueError(msg)

    pnl_by_date: dict[date, float] = {}
    for trade in trades:
        pnl_by_date[trade.trading_date] = pnl_by_date.get(trade.trading_date, 0.0) + trade.pnl

    date_range = pd.date_range(start=start, end=end, freq='B')
    if len(date_range) == 0:
        return ()

    points: list[EquityPoint] = []
    equity = initial_capital
    for ts in date_range:
        d = ts.date()
        daily_pnl = pnl_by_date.get(d, 0.0)
        prev_equity = equity
        equity = prev_equity + daily_pnl
        daily_return = daily_pnl / prev_equity if prev_equity != 0.0 else 0.0
        points.append(EquityPoint(
            trading_date=d,
            equity=equity,
            daily_pnl=daily_pnl,
            daily_return=daily_return,
        ))

    return tuple(points)
