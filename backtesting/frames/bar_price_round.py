"""Cent-align bar and indicator prices (2 decimals) for the backtest engine.

Cold Parquet keeps ``float32`` OHLC without cent rounding on write. After load (and
after feature assignment), prices are rounded here so indicators, triggers, and views
share one tick grid. OHLC columns are repaired so ``high`` / ``low`` remain valid
after independent rounding.
"""

import pandas as pd

from backtesting.frames.symbol_bar_frame import SymbolBarFrame

PRICE_DECIMALS = 2

OHLC_PRICE_COLUMNS: tuple[str, ...] = ('open', 'high', 'low', 'close')
LOADED_BAR_PRICE_COLUMNS: tuple[str, ...] = (*OHLC_PRICE_COLUMNS, 'vwap')
INDICATOR_PRICE_PREFIXES: tuple[str, ...] = ('ema',)

_RATIO_COLUMNS: frozenset[str] = frozenset({'rvol', 'rvol_time', 'adr', 'atr'})
_LARGE_COUNT_COLUMNS: frozenset[str] = frozenset({'volume', 'cumulative_avg_volume'})

# Terminal-friendly header labels (values keep full ``df`` column names).
INSPECT_HEADER_ALIASES: dict[str, str] = {
    'datetime_pt': 'time',
    'cumulative_avg_volume': 'cum_avg_vol',
    'signal_eligible': 'sig_elig',
    'all_filters_ok': 'all_filt',
    'all_triggers_ok': 'all_trig',
    'armed': 'armed',
    'entry_signal': 'entry_sig',
    'entry_event': 'entry_evt',
    'strategy_fired_today': 'fired_td',
}

INSPECT_SIGNAL_COLUMN_SUFFIX_ORDER: tuple[str, ...] = (
    'filter_',
    'trigger_',
    'all_filters_ok',
    'all_triggers_ok',
    'armed',
    'entry_signal',
    'strategy_fired_today',
    'entry_event',
)

INSPECT_LEFT_ALIGN_HEADERS: frozenset[str] = frozenset(
    {'symbol', 'datetime_pt', 'time', 'session'},
)

INSPECT_COLUMN_ORDER: tuple[str, ...] = (
    'time',
    'open',
    'high',
    'low',
    'close',
    'volume',
    'trading_date',
    'vwap',
    'ema9',
    'ema21',
    'ema50',
    'adr',
    'atr',
    'rvol',
    'rvol_time',
    'cumulative_avg_volume',
    'session',
    'signal_eligible',
    'all_filters_ok',
    'armed',
    'entry_signal',
    'strategy_fired_today',
    'entry_event',
)


def is_price_display_column(col: str, dtype: object) -> bool:
    """True for OHLC, vwap, and ema* columns (not volume, dates, or booleans)."""
    if col in ('symbol', 'datetime_pt', 'time', 'volume', 'trading_date'):
        return False
    if pd.api.types.is_bool_dtype(dtype):
        return False
    return col in LOADED_BAR_PRICE_COLUMNS or col.startswith(INDICATOR_PRICE_PREFIXES)


def round_price_series(series: pd.Series) -> pd.Series:
    """Round a price series to :data:`PRICE_DECIMALS` as ``float64`` (exact cents).

    Parquet stores ``float32``; after load prices are promoted to cent-aligned
    ``float64`` so indicators and comparisons do not inherit float32 noise.
    """
    return series.astype('float64').round(PRICE_DECIMALS)


def _repair_ohlc_high_low(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure ``high`` / ``low`` bracket rounded ``open`` and ``close``."""
    ohlc = df.loc[:, list(OHLC_PRICE_COLUMNS)].astype('float64')
    hi = ohlc.max(axis=1).round(PRICE_DECIMALS)
    lo = ohlc.min(axis=1).round(PRICE_DECIMALS)
    return df.assign(high=hi, low=lo)


def round_ohlc_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Round OHLC to cents and repair ``high`` / ``low`` invariants."""
    assign_kw = {col: round_price_series(df[col]) for col in OHLC_PRICE_COLUMNS if col in df.columns}
    if not assign_kw:
        return df
    return _repair_ohlc_high_low(df.assign(**assign_kw))


def round_loaded_bar_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Round cold-store OHLC and Parquet ``vwap`` after load (not used on ingest write)."""
    out = round_ohlc_columns(df)
    if 'vwap' in out.columns:
        out = out.assign(vwap=round_price_series(out.vwap))
    return out


def price_columns_on_frame(column_names: list[str]) -> tuple[str, ...]:
    """Price columns present on a frame (OHLC, vwap, ema*)."""
    out: list[str] = []
    for col in column_names:
        if col in LOADED_BAR_PRICE_COLUMNS:
            out.append(col)
            continue
        if any(col.startswith(prefix) for prefix in INDICATOR_PRICE_PREFIXES):
            out.append(col)
    return tuple(out)


def round_frame_price_columns(df: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """Round named price columns; runs OHLC repair when all four OHLC cols are included."""
    present_ohlc = [c for c in OHLC_PRICE_COLUMNS if c in columns and c in df.columns]
    assign_kw = {
        col: round_price_series(df[col])
        for col in columns
        if col in df.columns and col not in OHLC_PRICE_COLUMNS
    }
    if present_ohlc:
        ohlc_assign = {col: round_price_series(df[col]) for col in present_ohlc}
        out = df.assign(**ohlc_assign)
        if assign_kw:
            out = out.assign(**assign_kw)
        if len(present_ohlc) == len(OHLC_PRICE_COLUMNS):
            return _repair_ohlc_high_low(out)
        return out
    if not assign_kw:
        return df
    return df.assign(**assign_kw)


def normalize_symbol_bar_frame_prices(frame: SymbolBarFrame) -> SymbolBarFrame:
    """Round all price columns on ``frame.bars`` (loaded bars + indicator outputs)."""
    cols = price_columns_on_frame(frame.column_names)
    if not cols:
        return frame
    bars = round_frame_price_columns(frame.bars, cols)
    return SymbolBarFrame(
        symbol=frame.symbol,
        interval_minutes=frame.interval_minutes,
        bars=bars,
        daily_bars=frame.daily_bars,
        history_bars=frame.history_bars,
    )


def format_price_columns_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with price columns at two decimals; dates/bools unchanged."""
    df_show = df.copy()
    for col in df_show.columns:
        dtype = df_show[col].dtype
        if col == 'trading_date':
            df_show[col] = df_show[col].astype(str)
            continue
        if not is_price_display_column(col, dtype):
            continue
        df_show[col] = df_show[col].map(
            lambda v: '' if pd.isna(v) else f'{float(v):.{PRICE_DECIMALS}f}',
        )
    return df_show


def _is_inspect_bool_column(col: str, dtype: object) -> bool:
    if pd.api.types.is_bool_dtype(dtype):
        return True
    return col in {
        'signal_eligible',
        'all_filters_ok',
        'all_triggers_ok',
        'armed',
        'entry_signal',
        'strategy_fired_today',
        'entry_event',
    } or col.startswith(('trigger_', 'filter_'))


def _inspect_column_align(header: str) -> str:
    """Alignment for terminal columns: numeric headers match right-aligned values."""
    if header in INSPECT_LEFT_ALIGN_HEADERS:
        return 'left'
    if header.startswith(('flt_', 'trg_')) or header in {
        'sig_elig',
        'all_filt',
        'all_trig',
        'armed',
        'entry_sig',
        'entry_evt',
        'fired_td',
    }:
        return 'center'
    return 'right'


def _pad_inspect_field(header: str, text: str, width: int) -> str:
    """Pad one cell; truncate when the column width is narrower than the value."""
    clipped = text[:width]
    align = _inspect_column_align(header)
    if align == 'right':
        return clipped.rjust(width)
    if align == 'center':
        return clipped.center(width)
    return clipped.ljust(width)


def _format_inspect_cell(col: str, value: object, dtype: object) -> str:
    """One display string for ``inspect_indicator_bars`` (padding applied in ``inspect_bar_table_string``)."""
    if pd.isna(value):
        return ''
    if col == 'symbol':
        return str(value)[:6]
    if col in ('datetime_pt', 'time'):
        text = str(value)
        if len(text) >= 16:
            return text[11:16]
        return text[:5]
    if col == 'session':
        return str(value)[:3]
    if col == 'trading_date':
        return str(value)[:10]
    if _is_inspect_bool_column(col, dtype):
        return 'True' if bool(value) else 'False'
    if col in _LARGE_COUNT_COLUMNS:
        return f'{float(pd.to_numeric(value)):,.0f}'
    if col in _RATIO_COLUMNS:
        return f'{float(pd.to_numeric(value)):.{PRICE_DECIMALS}f}'
    if col == 'volume':
        return f'{int(pd.to_numeric(value)):,}'
    if is_price_display_column(col, dtype):
        return f'{float(pd.to_numeric(value)):.{PRICE_DECIMALS}f}'
    return str(value)[:16]


def _inspect_header_label(col: str) -> str:
    """Short column title for terminal tables (avoids pandas padding on long trigger ids)."""
    if col in INSPECT_HEADER_ALIASES:
        return INSPECT_HEADER_ALIASES[col]
    if col.startswith('trigger_'):
        trigger_id = col.removeprefix('trigger_')
        if '_cross_above_' in trigger_id:
            left, right = trigger_id.split('_cross_above_', 1)

            def _ema_tag(part: str) -> str:
                return part.removeprefix('ema') if part.startswith('ema') else part[:4]

            return f'trg_{_ema_tag(left)}x{_ema_tag(right)}'
        return f'trg_{trigger_id[:10]}'
    if col.startswith('filter_'):
        return f'flt_{col.removeprefix("filter_")}'
    return col


def order_inspect_display_columns(columns: tuple[str, ...]) -> tuple[str, ...]:
    """OHLCV first, then session/signal diagnostics, then any other columns."""
    preferred = [c for c in INSPECT_COLUMN_ORDER if c in columns]
    preferred_set = set(preferred)
    signal_cols: list[str] = []
    for suffix in INSPECT_SIGNAL_COLUMN_SUFFIX_ORDER:
        if suffix.endswith('_'):
            signal_cols.extend(
                sorted(c for c in columns if c.startswith(suffix) and c not in preferred_set),
            )
        elif suffix in columns and suffix not in preferred_set:
            signal_cols.append(suffix)
    signal_set = set(signal_cols)
    rest = sorted(c for c in columns if c not in preferred_set and c not in signal_set)
    return tuple(preferred + signal_cols + rest)


def format_inspect_bar_table(df: pd.DataFrame) -> pd.DataFrame:
    """All-string table with fixed-width cells for aligned terminal printing."""
    ordered = order_inspect_display_columns(tuple(df.columns))
    df_ordered = df.loc[:, list(ordered)]
    rows: dict[str, list[str]] = {}
    for col in df_ordered.columns:
        dtype = df_ordered[col].dtype
        rows[col] = [
            _format_inspect_cell(col, value, dtype) for value in df_ordered[col].tolist()
        ]
    df_strings = pd.DataFrame(rows, index=df_ordered.index)
    return df_strings.rename(columns={col: _inspect_header_label(col) for col in df_strings.columns})


def inspect_bar_table_string(df: pd.DataFrame) -> str:
    """Aligned multi-column bar dump for CLI (no DataFrame index column)."""
    table = format_inspect_bar_table(df)
    columns = list(table.columns)
    widths = [
        max(len(col), int(table[col].astype(str).str.len().max()) if len(table) else 0)
        for col in columns
    ]

    def _format_row(values: list[str]) -> str:
        padded = [
            _pad_inspect_field(col, str(value), width)
            for col, value, width in zip(columns, values, widths, strict=True)
        ]
        return ' '.join(padded)

    lines = [_format_row(columns)]
    for row in table.itertuples(index=False, name=None):
        lines.append(_format_row([str(value) for value in row]))
    return '\n'.join(lines)
