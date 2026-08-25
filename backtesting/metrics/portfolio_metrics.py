"""Portfolio-level statistics computed from the equity curve and closed trades."""

import math
from dataclasses import dataclass

from backtesting.metrics.equity_curve import EquityPoint
from backtesting.portfolio.trade import Trade

_TRADING_DAYS_PER_YEAR = 252.0


@dataclass(frozen=True)
class PortfolioMetrics:
    """Aggregate statistics for one backtest run."""

    initial_capital: float
    final_equity: float
    total_pnl: float
    total_return_pct: float
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float
    avg_win_pnl: float
    avg_loss_pnl: float
    max_drawdown_pct: float  # negative fraction e.g. -0.12 means -12%
    sharpe_ratio: float | None  # None when fewer than 2 trading days or zero variance
    sortino_ratio: float | None  # None when no negative return days
    calmar_ratio: float | None  # None when max drawdown is zero


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std_sample(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _max_drawdown(equity_curve: tuple[EquityPoint, ...]) -> float:
    """Worst peak-to-trough decline as a negative fraction."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0].equity
    worst = 0.0
    for point in equity_curve:
        if point.equity > peak:
            peak = point.equity
        if peak > 0.0:
            dd = (point.equity - peak) / peak
            if dd < worst:
                worst = dd
    return worst


def _annualized_return(total_return: float, n_trading_days: int) -> float:
    if n_trading_days < 2:
        return 0.0
    years = n_trading_days / _TRADING_DAYS_PER_YEAR
    return (1.0 + total_return) ** (1.0 / years) - 1.0


def compute_portfolio_metrics(
    equity_curve: tuple[EquityPoint, ...],
    trades: tuple[Trade, ...],
    initial_capital: float,
) -> 'PortfolioMetrics':
    """Compute all portfolio-level statistics from equity curve and trades."""
    final_equity = equity_curve[-1].equity if equity_curve else initial_capital
    total_pnl = final_equity - initial_capital
    total_return_pct = total_pnl / initial_capital if initial_capital else 0.0

    wins = [t for t in trades if t.pnl > 0.0]
    losses = [t for t in trades if t.pnl <= 0.0]
    trade_count = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / trade_count if trade_count else 0.0
    avg_win_pnl = _mean([t.pnl for t in wins])
    avg_loss_pnl = _mean([t.pnl for t in losses])

    max_dd = _max_drawdown(equity_curve)

    daily_returns = [p.daily_return for p in equity_curve]
    n_days = len(daily_returns)

    sharpe: float | None = None
    sortino: float | None = None
    calmar: float | None = None

    if n_days >= 2:
        mean_r = _mean(daily_returns)
        std_r = _std_sample(daily_returns)
        if std_r > 0.0:
            sharpe = mean_r / std_r * math.sqrt(_TRADING_DAYS_PER_YEAR)

        # Sortino: downside deviation using returns below MAR=0
        downside_sq = [min(r, 0.0) ** 2 for r in daily_returns]
        downside_dev = math.sqrt(_mean(downside_sq))
        if downside_dev > 0.0:
            sortino = mean_r / downside_dev * math.sqrt(_TRADING_DAYS_PER_YEAR)

    if max_dd < 0.0 and n_days >= 2:
        ann_r = _annualized_return(total_return_pct, n_days)
        calmar = ann_r / abs(max_dd)

    return PortfolioMetrics(
        initial_capital=initial_capital,
        final_equity=final_equity,
        total_pnl=total_pnl,
        total_return_pct=total_return_pct,
        trade_count=trade_count,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=win_rate,
        avg_win_pnl=avg_win_pnl,
        avg_loss_pnl=avg_loss_pnl,
        max_drawdown_pct=max_dd,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
    )
