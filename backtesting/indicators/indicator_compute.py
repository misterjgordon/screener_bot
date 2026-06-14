"""Build ``IndicatorSpec.compute_fn`` from catalog entries."""

from collections.abc import Callable
from typing import TYPE_CHECKING

import pandas as pd

from backtesting.indicators.indicator_catalog_load import IndicatorCatalogEntry
from backtesting.indicators.indicator_catalog_load import IndicatorCatalogError
from backtesting.indicators.indicator_catalog_load import resolve_series_callable
from backtesting.indicators.indicator_spec import IndicatorKind
from backtesting.indicators.indicator_spec import IndicatorSpec

if TYPE_CHECKING:
    import pandas as pd

    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.indicators.indicator_catalog_models import IndicatorSeriesKwarg


def _series_kwargs(
    frame: 'SymbolBarFrame',
    entry: IndicatorCatalogEntry,
) -> dict[str, 'IndicatorSeriesKwarg']:
    """Build kwargs for ``series_fn`` from frame columns and catalog maps."""
    kw: dict[str, IndicatorSeriesKwarg] = dict(entry.params)
    for arg_name, col_name in entry.inputs.items():
        if col_name not in frame.bars.columns:
            msg = (
                f'Indicator {entry.id!r} input {arg_name!r} needs column {col_name!r} '
                f'on frame.bars; have {frame.column_names}'
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


def compute_indicator_series(
    frame: 'SymbolBarFrame',
    entry: IndicatorCatalogEntry,
) -> 'pd.Series':
    """Evaluate one catalog indicator; returns the output column series."""
    if entry.bar_interval_minutes != frame.interval_minutes:
        msg = (
            f'Indicator {entry.id!r} expects bar_interval_minutes={entry.bar_interval_minutes}, '
            f'frame has {frame.interval_minutes}'
        )
        raise IndicatorCatalogError(msg)
    if len(entry.outputs) != 1:
        msg = f'Indicator {entry.id!r} must have exactly one output column for generic adapter'
        raise IndicatorCatalogError(msg)
    series_fn = resolve_series_callable(entry.series_fn)
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
