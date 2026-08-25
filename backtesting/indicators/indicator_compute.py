"""Build ``IndicatorSpec.compute_fn`` from catalog entries."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from backtesting.indicators.indicator_catalog_load import IndicatorCatalogEntry
from backtesting.indicators.indicator_catalog_load import IndicatorCatalogError
from backtesting.indicators.indicator_catalog_load import resolve_series_callable
from backtesting.indicators.indicator_spec import IndicatorKind
from backtesting.indicators.indicator_spec import IndicatorSpec
from strategies.indicators.bar_resample import resample_to_interval

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.indicators.indicator_catalog_models import IndicatorSeriesKwarg


@dataclass
class _FrameLike:
    """Minimal frame duck-type used when passing resampled bars to ``_series_kwargs``."""

    bars: pd.DataFrame
    daily_bars: 'pd.DataFrame | None'
    history_bars: 'pd.DataFrame | None'


def _series_kwargs(
    frame: 'SymbolBarFrame | _FrameLike',
    entry: IndicatorCatalogEntry,
) -> dict[str, 'IndicatorSeriesKwarg']:
    """Build kwargs for ``series_fn`` from frame columns and catalog maps."""
    kw: dict[str, IndicatorSeriesKwarg] = dict(entry.params)
    for arg_name, col_name in entry.inputs.items():
        if col_name not in frame.bars.columns:
            msg = (
                f'Indicator {entry.id!r} input {arg_name!r} needs column {col_name!r} '
                f'on frame.bars; have {list(frame.bars.columns)}'
            )
            raise IndicatorCatalogError(msg)
        kw[arg_name] = frame.bars[col_name]

    if entry.requires_daily_bars:
        if frame.daily_bars is None or frame.daily_bars.empty:
            msg = f'Indicator {entry.id!r} requires daily_bars on SymbolBarFrame'
            raise IndicatorCatalogError(msg)
        kw['daily_bars'] = frame.daily_bars
        for arg_name, col_name in entry.daily_inputs.items():
            if col_name not in frame.daily_bars.columns:
                msg = (
                    f'Indicator {entry.id!r} daily input {arg_name!r} needs {col_name!r} '
                    f'on daily_bars'
                )
                raise IndicatorCatalogError(msg)
            kw[arg_name] = frame.daily_bars[col_name]

    if entry.requires_history_bars:
        if frame.history_bars is None or frame.history_bars.empty:
            msg = f'Indicator {entry.id!r} requires history_bars on SymbolBarFrame'
            raise IndicatorCatalogError(msg)
        kw['history_bars'] = frame.history_bars

    return kw


def _broadcast_to_original(
    series_resampled: pd.Series,
    df_resampled: pd.DataFrame,
    df_original: pd.DataFrame,
) -> pd.Series:
    """Map higher-TF output values back to each 1-min bar via backward asof join on timestamp."""
    lookup = df_resampled[['timestamp', 'trading_date']].assign(_value=series_resampled.values)

    orig = df_original[['timestamp', 'trading_date']].copy()
    orig['_pos'] = range(len(orig))

    merged = pd.merge_asof(
        orig.sort_values('timestamp'),
        lookup.sort_values('timestamp'),
        on='timestamp',
        by='trading_date',
        direction='backward',
    )
    return pd.Series(
        merged.sort_values('_pos')['_value'].values,
        index=df_original.index,
    )


def compute_indicator_series(
    frame: 'SymbolBarFrame',
    entry: IndicatorCatalogEntry,
) -> pd.Series:
    """Evaluate one catalog indicator; returns the output column series.

    When ``entry.bar_interval_minutes`` differs from ``frame.interval_minutes``, bars are
    aggregated to the required interval, the series function is run on the resampled frame,
    and the result is broadcast back to the original 1-min index via backward asof join.
    """
    if len(entry.outputs) != 1:
        msg = f'Indicator {entry.id!r} must have exactly one output column for generic adapter'
        raise IndicatorCatalogError(msg)

    series_fn = resolve_series_callable(entry.series_fn)

    if entry.bar_interval_minutes != frame.interval_minutes:
        if entry.bar_interval_minutes < frame.interval_minutes:
            msg = (
                f'Indicator {entry.id!r} requires bar_interval_minutes='
                f'{entry.bar_interval_minutes} but frame has {frame.interval_minutes}; '
                f'downsampling is not supported'
            )
            raise IndicatorCatalogError(msg)
        df_resampled = resample_to_interval(frame.bars, entry.bar_interval_minutes)
        resampled = _FrameLike(
            bars=df_resampled,
            daily_bars=frame.daily_bars,
            history_bars=frame.history_bars,
        )
        output_resampled = series_fn(**_series_kwargs(resampled, entry))
        return _broadcast_to_original(output_resampled, df_resampled, frame.bars)

    return series_fn(**_series_kwargs(frame, entry))


def make_indicator_compute_fn(entry: IndicatorCatalogEntry) -> Callable[['SymbolBarFrame'], 'SymbolBarFrame']:
    """Return a picklable compute function for one catalog entry."""
    output_col = entry.outputs[0]

    def compute(frame: 'SymbolBarFrame') -> 'SymbolBarFrame':
        series = compute_indicator_series(frame, entry)
        return frame.with_columns(**{output_col: series})

    compute.__name__ = f'_compute_{entry.id}'
    compute.__qualname__ = compute.__name__
    return compute


def indicator_spec_from_catalog_entry(entry: IndicatorCatalogEntry) -> IndicatorSpec:
    """Build ``IndicatorSpec`` for registry registration."""
    kind = IndicatorKind.SESSION_COLUMN if entry.kind == 'session_column' else IndicatorKind.INDICATOR
    input_cols = tuple(sorted(set(entry.inputs.values())))
    return IndicatorSpec(
        id=entry.id,
        kind=kind,
        inputs=input_cols,
        outputs=entry.outputs,
        description=entry.description,
        version=entry.version,
        compute_fn=make_indicator_compute_fn(entry),
    )
