"""Per-symbol bar walk: ``entry_event`` rows → closed trades."""

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from backtesting.conditions.session_regime import SESSION_COLUMN
from backtesting.portfolio.exit_levels import ExitLevels
from backtesting.portfolio.exit_levels import close_past_ema_rule
from backtesting.portfolio.exit_levels import exit_levels
from backtesting.portfolio.exit_levels import has_end_of_session_other
from backtesting.portfolio.trade import ExitReason
from backtesting.portfolio.trade import Trade
from backtesting.signals.entry_columns import ENTRY_EVENT_COLUMN
from backtesting.signals.entry_columns import TRADING_DATE_COLUMN
from backtesting.signals.signal_columns import SIGNAL_EXIT_HIT_COLUMN
from backtesting.strategy.strategy_config import SizingFixedDollars
from backtesting.strategy.strategy_config import SizingFullAllocation
from strategies.exit.other.close_past_ema import close_past_ema_exit_series

if TYPE_CHECKING:

    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.strategy.strategy_config import SessionLabel
    from backtesting.strategy.strategy_config import SizingConfig
    from backtesting.strategy.strategy_config import StrategyConfig
    from backtesting.strategy.strategy_config import StrategySide


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


def _shares_for_entry(sizing: 'SizingConfig', entry_price: float, *, capital: float | None) -> float:
    if entry_price <= 0:
        msg = f'entry_price must be > 0 for sizing, got {entry_price}'
        raise PortfolioSimError(msg)
    if isinstance(sizing, SizingFixedDollars):
        return sizing.amount / entry_price
    if isinstance(sizing, SizingFullAllocation):
        if capital is None:
            msg = 'sizing.method=full_allocation requires capital to be passed to the simulator'
            raise PortfolioSimError(msg)
        if capital <= 0:
            msg = f'capital must be > 0 for full_allocation sizing, got {capital}'
            raise PortfolioSimError(msg)
        return capital / entry_price
    msg = f'Unsupported sizing.method: {sizing.method!r}'
    raise PortfolioSimError(msg)


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


def _exit_on_bar(
    *,
    side: 'StrategySide',
    low: float,
    high: float,
    close: float,
    levels: ExitLevels,
    close_past_ema_hit: bool,
    signal_exit_hit: bool,
    is_last_session_exit_bar: bool,
    end_of_session_enabled: bool,
) -> tuple[float, ExitReason] | None:
    """First hit wins: stop, target, close-past-EMA, YAML exit signal, then end of session."""
    if side == 'long':
        stop_hit = low <= levels.stop_price
        target_hit = high >= levels.take_profit_price
    else:
        stop_hit = high >= levels.stop_price
        target_hit = low <= levels.take_profit_price
    if stop_hit:
        return levels.stop_price, 'stop_loss'
    if target_hit:
        return levels.take_profit_price, 'take_profit'
    if close_past_ema_hit:
        return _bar_fill_price(close), 'close_past_ema'
    if signal_exit_hit:
        return _bar_fill_price(close), 'signal_exit'
    if is_last_session_exit_bar and end_of_session_enabled:
        return _bar_fill_price(close), 'end_of_session'
    return None


def _trade_pnl(*, side: 'StrategySide', entry_price: float, exit_price: float, shares: float) -> tuple[float, float]:
    """(pnl, pnl_pct) signed for the entry side."""
    if side == 'long':
        pnl = (exit_price - entry_price) * shares
        pnl_pct = (exit_price - entry_price) / entry_price
    else:
        pnl = (entry_price - exit_price) * shares
        pnl_pct = (entry_price - exit_price) / entry_price
    return pnl, pnl_pct


def _simulate_one_trade(
    bars: 'pd.DataFrame',
    *,
    symbol: str,
    side: 'StrategySide',
    entry_pos: int,
    levels: ExitLevels,
    scan_end_pos: int,
    last_session_exit_pos: int | None,
    shares: float,
    end_of_session_enabled: bool,
    close_past_ema_hits: 'pd.Series | None',
    signal_exit_hits: 'pd.Series | None',
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
        close_past_ema_hit = bool(close_past_ema_hits.iloc[pos]) if close_past_ema_hits is not None else False
        signal_exit_hit = bool(signal_exit_hits.iloc[pos]) if signal_exit_hits is not None else False
        hit = _exit_on_bar(
            side=side,
            low=float(row.low),
            high=float(row.high),
            close=float(row.close),
            levels=levels,
            close_past_ema_hit=close_past_ema_hit,
            signal_exit_hit=signal_exit_hit,
            is_last_session_exit_bar=is_last_session_bar,
            end_of_session_enabled=end_of_session_enabled,
        )
        if hit is None:
            continue
        exit_price, exit_reason = hit
        pnl, pnl_pct = _trade_pnl(side=side, entry_price=entry_price, exit_price=exit_price, shares=shares)
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


@dataclass(frozen=True)
class SymbolExitContext:
    """Precomputed per-frame exit data, independent of which entry candidate is resolved.

    Built once per symbol and reused across every entry candidate on that symbol
    (whole-frame walk, or one candidate at a time from a merged cross-symbol stream).
    """

    bars: 'pd.DataFrame'
    symbol: str
    side: 'StrategySide'
    allowed_sessions: tuple['SessionLabel', ...]
    last_bar_by_date: dict[date, int]
    end_of_session_enabled: bool
    last_session_exit: dict[date, int]
    close_past_ema_hits: 'pd.Series | None'
    signal_exit_hits: 'pd.Series | None'


def build_symbol_exit_context(frame: 'SymbolBarFrame', strategy: 'StrategyConfig') -> SymbolExitContext:
    """Validate ``frame.bars`` and precompute exit-check data for one symbol.

    Raises :class:`PortfolioSimError` for a misconfigured strategy (missing bar columns)
    regardless of whether the frame has any entry candidates.
    """
    bars = frame.bars
    symbol = symbol_from_frame(frame)
    if ENTRY_EVENT_COLUMN not in bars.columns:
        msg = f'{symbol}: missing {ENTRY_EVENT_COLUMN!r}; run SignalPipeline first'
        raise PortfolioSimError(msg)

    allowed_sessions = strategy.session_config.allowed_sessions
    last_bar_by_date = _last_bar_position_by_trading_date(bars)
    end_of_session_enabled = has_end_of_session_other(strategy.other_exits)
    last_session_exit: dict[date, int] = {}
    if end_of_session_enabled:
        last_session_exit = _last_session_exit_position_by_trading_date(bars, allowed_sessions)

    close_past_ema_hits: pd.Series | None = None
    close_past_ema = close_past_ema_rule(strategy.other_exits)
    if close_past_ema is not None:
        if close_past_ema.ema_column not in bars.columns:
            msg = f'{symbol}: missing {close_past_ema.ema_column!r} for close_past_ema exit'
            raise PortfolioSimError(msg)
        close_past_ema_hits = close_past_ema_exit_series(
            bars.close,
            bars[close_past_ema.ema_column],
            side=close_past_ema.side,
        )

    signal_exit_hits: pd.Series | None = None
    if SIGNAL_EXIT_HIT_COLUMN in bars.columns:
        signal_exit_hits = bars[SIGNAL_EXIT_HIT_COLUMN].astype(bool)

    return SymbolExitContext(
        bars=bars,
        symbol=symbol,
        side=strategy.side,
        allowed_sessions=allowed_sessions,
        last_bar_by_date=last_bar_by_date,
        end_of_session_enabled=end_of_session_enabled,
        last_session_exit=last_session_exit,
        close_past_ema_hits=close_past_ema_hits,
        signal_exit_hits=signal_exit_hits,
    )


def entry_candidate_positions(bars: 'pd.DataFrame') -> tuple[int, ...]:
    """``iloc`` positions of every ``entry_event`` row, in bar order."""
    if ENTRY_EVENT_COLUMN not in bars.columns:
        msg = f'missing {ENTRY_EVENT_COLUMN!r}; run SignalPipeline first'
        raise PortfolioSimError(msg)
    entry_mask = bars[ENTRY_EVENT_COLUMN].astype(bool)
    return tuple(int(bars.index.get_loc(idx)) for idx in bars.index[entry_mask])


def resolve_trade_for_candidate(
    ctx: SymbolExitContext,
    entry_pos: int,
    strategy: 'StrategyConfig',
    *,
    capital: float | None,
) -> Trade:
    """Resolve one entry candidate (``entry_pos`` on ``ctx.bars``) into a closed :class:`Trade`."""
    bars = ctx.bars
    entry_row = bars.iloc[entry_pos]
    td = _trading_date_key(entry_row[TRADING_DATE_COLUMN])
    if td not in ctx.last_bar_by_date:
        msg = f'{ctx.symbol} {td}: no bars for trading_date on loaded frame'
        raise PortfolioSimError(msg)
    if ctx.end_of_session_enabled and td not in ctx.last_session_exit:
        msg = (
            f'{ctx.symbol} {td}: no bars in allowed sessions {list(ctx.allowed_sessions)!r} '
            'for end_of_session exit'
        )
        raise PortfolioSimError(msg)

    entry_price = _bar_fill_price(float(entry_row.close))
    shares = _shares_for_entry(strategy.sizing, entry_price, capital=capital)
    levels = exit_levels(entry_price, strategy.stop_loss, strategy.take_profit, strategy.side)
    return _simulate_one_trade(
        bars,
        symbol=ctx.symbol,
        side=ctx.side,
        entry_pos=entry_pos,
        levels=levels,
        scan_end_pos=ctx.last_bar_by_date[td],
        last_session_exit_pos=ctx.last_session_exit.get(td),
        shares=shares,
        end_of_session_enabled=ctx.end_of_session_enabled,
        close_past_ema_hits=ctx.close_past_ema_hits,
        signal_exit_hits=ctx.signal_exit_hits,
    )


def simulate_symbol_trades(
    frame: 'SymbolBarFrame',
    strategy: 'StrategyConfig',
    *,
    capital: float | None = None,
) -> tuple[Trade, ...]:
    """Walk ``frame.bars`` and emit one trade per ``entry_event`` row (``strategy.side`` direction).

    ``capital`` is required when ``strategy.sizing.method == 'full_allocation'`` (account-level
    capital allocated to this strategy for the run); ignored for ``fixed_dollars`` sizing.
    """
    if frame.bars.empty:
        return ()
    ctx = build_symbol_exit_context(frame, strategy)
    positions = entry_candidate_positions(ctx.bars)
    return tuple(
        resolve_trade_for_candidate(ctx, pos, strategy, capital=capital)
        for pos in positions
    )
