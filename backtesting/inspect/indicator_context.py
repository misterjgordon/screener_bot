"""Inspect helpers: daily/history context notes for indicator columns."""

from typing import TYPE_CHECKING

from backtesting.indicators.indicator_catalog_load import catalog_entry_by_id
from backtesting.indicators.indicator_catalog_load import min_daily_sessions_for_indicators
from backtesting.indicators.indicator_catalog_load import min_history_sessions_for_indicators
from backtesting.indicators.indicator_registry import INDICATOR_REGISTRY
from strategies.indicators.trading_date import trading_date_series_utc

if TYPE_CHECKING:
    import pandas as pd

    from backtesting.frames.symbol_bar_frame import SymbolBarFrame


def _daily_session_count(daily_bars: 'pd.DataFrame | None') -> int:
    if daily_bars is None or daily_bars.empty:
        return 0
    if 'trading_date' in daily_bars.columns:
        return int(daily_bars.trading_date.nunique())
    return len(daily_bars)


def _history_session_count(history_bars: 'pd.DataFrame | None') -> int:
    if history_bars is None or history_bars.empty:
        return 0
    return int(trading_date_series_utc(history_bars.timestamp).nunique())


def indicator_context_notes(
    frame: 'SymbolBarFrame',
    indicator_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Notes when ``daily_bars`` / ``history_bars`` may leave context indicators NaN.

    ADR, ATR, RVOL, and ``cumulative_avg_volume`` need sufficient prior sessions in
    cold storage; a single display day can still show NaN on early minutes when history
    is thin.
    """
    catalog = catalog_entry_by_id()
    notes: list[str] = []

    daily_indicator_ids = tuple(
        ind_id
        for ind_id in indicator_ids
        if ind_id in catalog and catalog[ind_id].requires_daily_bars
    )
    if daily_indicator_ids:
        session_count = _daily_session_count(frame.daily_bars)
        min_sessions = min_daily_sessions_for_indicators()
        output_cols = INDICATOR_REGISTRY.output_columns_for(daily_indicator_ids)
        if session_count < min_sessions:
            notes.append(
                f'daily_context: {session_count} RTH sessions in daily_bars '
                f'(need >={min_sessions} for {list(daily_indicator_ids)}) — '
                f'{list(output_cols)} may be NaN'
            )
        else:
            notes.append(
                f'daily_context: {session_count} RTH sessions in daily_bars '
                f'(>={min_sessions} required for adr/atr)'
            )

    history_indicator_ids = tuple(
        ind_id
        for ind_id in indicator_ids
        if ind_id in catalog and catalog[ind_id].requires_history_bars
    )
    if history_indicator_ids:
        session_count = _history_session_count(frame.history_bars)
        min_sessions = min_history_sessions_for_indicators()
        output_cols = INDICATOR_REGISTRY.output_columns_for(history_indicator_ids)
        if session_count < min_sessions:
            notes.append(
                f'history_context: {session_count} ET session dates in history_bars '
                f'(need >={min_sessions} for {list(history_indicator_ids)}) — '
                f'{list(output_cols)} may be NaN'
            )
        else:
            notes.append(
                f'history_context: {session_count} ET session dates in history_bars '
                f'(>={min_sessions} required for rvol / cum_avg_vol)'
            )

    return tuple(notes)


def display_window_nan_columns(
    df_display: 'pd.DataFrame',
    indicator_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Output columns that are all-NaN in the printed window (diagnostic)."""
    if df_display.empty:
        return ()
    cols = INDICATOR_REGISTRY.output_columns_for(indicator_ids)
    out: list[str] = []
    for col in cols:
        if col not in df_display.columns:
            continue
        series = df_display[col]
        if series.isna().all():
            out.append(col)
    return tuple(out)
