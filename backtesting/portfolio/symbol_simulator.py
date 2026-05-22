"""Per-symbol bar walk: ``entry_event`` rows → closed trades."""

from datetime import date
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from backtesting.conditions.session_regime import SESSION_COLUMN
from backtesting.portfolio.exit_levels import ExitLevels
from backtesting.portfolio.exit_levels import exit_levels_for_long
from backtesting.portfolio.exit_levels import has_end_of_session_other
from backtesting.portfolio.trade import ExitReason
from backtesting.portfolio.trade import Trade
from backtesting.signals.entry_columns import ENTRY_EVENT_COLUMN
from backtesting.signals.entry_columns import TRADING_DATE_COLUMN

if TYPE_CHECKING:

    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.strategy.strategy_config import SessionLabel
    from backtesting.strategy.strategy_config import SizingConfig
    from backtesting.strategy.strategy_config import StrategyConfig


class PortfolioSimError(Exception):
    """Invalid bars or strategy for simulation."""


def _trading_date_key(value: object) -> date:
    """Normalize ``trading_date`` cell to :class:`datetime.date`."""
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, pd.Timestamp):
        return value.date()
    return pd.Timestamp(str(value)).date()


def symbol_from_frame(frame: 'SymbolBarFrame') -> str:
    """Ticker from ``bars.symbol`` (cold Parquet column), not frame metadata alone."""
    bars = frame.bars
    if 'symbol' not in bars.columns:
        return frame.symbol.strip().upper()
    values = bars.symbol.astype(str).str.strip().str.upper().unique()
    if len(values) != 1:
        msg = f'expected one symbol in bars.symbol, got {list(values)!r}'
        raise PortfolioSimError(msg)
    return str(values[0])


def _bar_fill_price(close: float) -> float:
    """Fill at the bar close (bars are already cent-aligned)."""
    return float(close)


def _shares_for_entry(sizing: 'SizingConfig', entry_price: float) -> float:
    if sizing.method != 'fixed_dollars':
        msg = f'Unsupported sizing.method: {sizing.method!r}'
        raise PortfolioSimError(msg)
    if entry_price <= 0:
        msg = f'entry_price must be > 0 for sizing, got {entry_price}'
        raise PortfolioSimError(msg)
    return sizing.amount / entry_price


def _last_bar_position_by_trading_date(bars: 'pd.DataFrame') -> dict[date, int]:
    """Last iloc position per ``trading_date`` on the loaded bar table."""
    if TRADING_DATE_COLUMN not in bars.columns:
        msg = f'bars missing {TRADING_DATE_COLUMN!r}'
        raise PortfolioSimError(msg)

    out: dict[date, int] = {}
    for td, group in bars.groupby(TRADING_DATE_COLUMN, sort=False):
        out[_trading_date_key(td)] = int(bars.index.get_loc(group.index[-1]))
    return out


def _last_session_exit_position_by_trading_date(
    bars: 'pd.DataFrame',
    allowed_sessions: tuple['SessionLabel', ...],
) -> dict[date, int]:
    """Last iloc position per ``trading_date`` in an allowed ``session`` (from bar data)."""
    if SESSION_COLUMN not in bars.columns:
        msg = f'bars missing {SESSION_COLUMN!r}; run ConditionPipeline(session_config=...) first'
        raise PortfolioSimError(msg)
    if TRADING_DATE_COLUMN not in bars.columns:
        msg = f'bars missing {TRADING_DATE_COLUMN!r}'
        raise PortfolioSimError(msg)

    in_session = bars[SESSION_COLUMN].isin(list(allowed_sessions))
    session_bars = bars.loc[in_session]
    if session_bars.empty:
        return {}

    out: dict[date, int] = {}
    for td, group in session_bars.groupby(TRADING_DATE_COLUMN, sort=False):
        out[_trading_date_key(td)] = int(bars.index.get_loc(group.index[-1]))
    return out


def _exit_on_bar_long(
    *,
    low: float,
    high: float,
    close: float,
    levels: ExitLevels,
    is_last_session_exit_bar: bool,
    end_of_session_enabled: bool,
) -> tuple[float, ExitReason] | None:
    """First hit wins: stop before target on the same bar (conservative for longs)."""
    if low <= levels.stop_price:
        return levels.stop_price, 'stop_loss'
    if high >= levels.take_profit_price:
        return levels.take_profit_price, 'take_profit'
    if is_last_session_exit_bar and end_of_session_enabled:
        return _bar_fill_price(close), 'end_of_session'
    return None


def _simulate_one_long_trade(
    bars: 'pd.DataFrame',
    *,
    symbol: str,
    entry_pos: int,
    levels: ExitLevels,
    scan_end_pos: int,
    last_session_exit_pos: int | None,
    shares: float,
    end_of_session_enabled: bool,
) -> Trade:
    entry_row = bars.iloc[entry_pos]
    entry_price = _bar_fill_price(float(entry_row.close))
    trading_date = _trading_date_key(entry_row[TRADING_DATE_COLUMN])
    entry_ts = entry_row.timestamp

    for pos in range(entry_pos, scan_end_pos + 1):
        row = bars.iloc[pos]
        is_last_session_bar = (
            end_of_session_enabled
            and last_session_exit_pos is not None
            and pos == last_session_exit_pos
        )
        hit = _exit_on_bar_long(
            low=float(row.low),
            high=float(row.high),
            close=float(row.close),
            levels=levels,
            is_last_session_exit_bar=is_last_session_bar,
            end_of_session_enabled=end_of_session_enabled,
        )
        if hit is None:
            continue
        exit_price, exit_reason = hit
        pnl = (exit_price - entry_price) * shares
        pnl_pct = (exit_price - entry_price) / entry_price
        return Trade(
            symbol=symbol,
            trading_date=trading_date,
            entry_timestamp_utc=entry_ts,
            entry_price=entry_price,
            exit_timestamp_utc=row.timestamp,
            exit_price=exit_price,
            exit_reason=exit_reason,
            shares=shares,
            pnl=pnl,
            pnl_pct=pnl_pct,
        )

    msg = (
        f'{symbol} {trading_date}: open position never closed '
        f'(entry_pos={entry_pos}, scan_end_pos={scan_end_pos})'
    )
    raise PortfolioSimError(msg)


def simulate_symbol_trades(frame: 'SymbolBarFrame', strategy: 'StrategyConfig') -> tuple[Trade, ...]:
    """Walk ``frame.bars`` and emit one trade per ``entry_event`` row (long-only MVP)."""
    bars = frame.bars
    symbol = symbol_from_frame(frame)
    if bars.empty:
        return ()
    if ENTRY_EVENT_COLUMN not in bars.columns:
        msg = f'{symbol}: missing {ENTRY_EVENT_COLUMN!r}; run SignalPipeline first'
        raise PortfolioSimError(msg)

    allowed_sessions = strategy.session_config.allowed_sessions
    last_bar_by_date = _last_bar_position_by_trading_date(bars)
    end_of_session_enabled = has_end_of_session_other(strategy.other_exits)
    last_session_exit: dict[date, int] = {}
    if end_of_session_enabled:
        last_session_exit = _last_session_exit_position_by_trading_date(bars, allowed_sessions)

    entry_mask = bars[ENTRY_EVENT_COLUMN].astype(bool)
    if not entry_mask.any():
        return ()

    trades: list[Trade] = []
    for entry_idx in bars.index[entry_mask]:
        entry_pos = int(bars.index.get_loc(entry_idx))
        entry_row = bars.iloc[entry_pos]
        td = _trading_date_key(entry_row[TRADING_DATE_COLUMN])
        if td not in last_bar_by_date:
            msg = f'{symbol} {td}: no bars for trading_date on loaded frame'
            raise PortfolioSimError(msg)
        if end_of_session_enabled and td not in last_session_exit:
            msg = (
                f'{symbol} {td}: no bars in allowed sessions {list(allowed_sessions)!r} '
                'for end_of_session exit'
            )
            raise PortfolioSimError(msg)

        entry_price = _bar_fill_price(float(entry_row.close))
        shares = _shares_for_entry(strategy.sizing, entry_price)
        levels = exit_levels_for_long(entry_price, strategy.stop_loss, strategy.take_profit)
        trades.append(
            _simulate_one_long_trade(
                bars,
                symbol=symbol,
                entry_pos=entry_pos,
                levels=levels,
                scan_end_pos=last_bar_by_date[td],
                last_session_exit_pos=last_session_exit.get(td),
                shares=shares,
                end_of_session_enabled=end_of_session_enabled,
            ),
        )

    return tuple(trades)
