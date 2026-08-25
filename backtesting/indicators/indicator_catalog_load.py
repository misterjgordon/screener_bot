"""Load and order ``indicator_catalog.yaml``."""

from functools import lru_cache
from importlib import import_module
from pathlib import Path

import yaml

from backtesting.indicators.indicator_catalog_models import IndicatorCatalogDocument
from backtesting.indicators.indicator_catalog_models import IndicatorCatalogEntry
from backtesting.indicators.indicator_catalog_models import IndicatorSeriesFn

P_INDICATOR_CATALOG = Path(__file__).resolve().parent / 'indicator_catalog.yaml'


class IndicatorCatalogError(ValueError):
    """Raised when the catalog file is missing or invalid."""


@lru_cache(maxsize=1)
def load_indicator_catalog_document() -> IndicatorCatalogDocument:
    """Parse ``indicator_catalog.yaml`` (cached)."""
    if not P_INDICATOR_CATALOG.is_file():
        msg = f'Indicator catalog not found: {P_INDICATOR_CATALOG}'
        raise IndicatorCatalogError(msg)
    with P_INDICATOR_CATALOG.open(encoding='utf-8') as yaml_file:
        raw = yaml.safe_load(yaml_file)
    if not isinstance(raw, dict):
        msg = 'indicator_catalog.yaml root must be a mapping'
        raise IndicatorCatalogError(msg)
    return IndicatorCatalogDocument.model_validate(raw)


def default_indicator_ids() -> tuple[str, ...]:
    """Pipeline ids from ``default_pipeline_ids`` in ``indicator_catalog.yaml``."""
    return load_indicator_catalog_document().default_pipeline_ids


def min_daily_sessions_for_indicators() -> int:
    """Minimum distinct RTH session rows in ``daily_bars`` for ADR/ATR to populate."""
    need = 0
    for entry in catalog_entry_by_id().values():
        if not entry.requires_daily_bars:
            continue
        days_param = entry.params.get('days')
        period_param = entry.params.get('period')
        if days_param is not None:
            need = max(need, int(days_param) + 1)
        if period_param is not None:
            need = max(need, int(period_param) + 2)
    return need


def min_history_sessions_for_indicators() -> int:
    """Minimum distinct ET session dates in ``history_bars`` (today + prior ``period``)."""
    need = 0
    for entry in catalog_entry_by_id().values():
        if not entry.requires_history_bars:
            continue
        period_param = entry.params.get('period')
        if period_param is not None:
            need = max(need, int(period_param) + 1)
    return need


def history_bar_lookback_calendar_days() -> int:
    """Calendar days of 1m Parquet to read so ``history_bars`` covers RVOL prior sessions."""
    session_need = min_history_sessions_for_indicators()
    # Weekends/holidays: ~1.4 calendar days per session; small buffer only (not 30d).
    return session_need + 8


def warmup_bars_for_indicators(indicator_ids: tuple[str, ...]) -> int:
    """Minimum 1m warmup bars so intraday indicators are non-NaN at analysis window start.

    Uses 3x the longest period for EMA-style indicators.
    History-based (RVOL) and daily-based (ADR/ATR) indicators are covered separately
    by ``history_bar_lookback_calendar_days`` and ``daily_bar_lookback_calendar_days``.
    """
    by_id = catalog_entry_by_id()
    max_warmup = 0
    for iid in indicator_ids:
        entry = by_id.get(iid)
        if entry is None or entry.requires_history_bars or entry.requires_daily_bars:
            continue
        period = entry.params.get('period')
        if period is not None:
            max_warmup = max(max_warmup, int(period) * 3)
    return max_warmup


def daily_bar_lookback_calendar_days() -> int:
    """Calendar days of daily (or 1m→daily) history for ``requires_daily_bars`` indicators."""
    need = min_daily_sessions_for_indicators()
    return max(30, need + 10)


def catalog_entry_by_id() -> dict[str, IndicatorCatalogEntry]:
    """All catalog entries keyed by id."""
    doc = load_indicator_catalog_document()
    return {entry.id: entry for entry in doc.indicators}


def resolve_series_callable(series_fn: str) -> IndicatorSeriesFn:
    """Import ``module.path:callable_name`` from catalog ``series_fn``."""
    if ':' not in series_fn:
        msg = f'series_fn must be module:callable, got {series_fn!r}'
        raise IndicatorCatalogError(msg)
    module_name, attr_name = series_fn.split(':', 1)
    module = import_module(module_name)
    fn = getattr(module, attr_name, None)
    if fn is None:
        msg = f'Callable {attr_name!r} not found in {module_name!r}'
        raise IndicatorCatalogError(msg)
    if not callable(fn):
        msg = f'series_fn {series_fn!r} is not callable'
        raise IndicatorCatalogError(msg)
    return fn


def topological_indicator_order(indicator_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Sort ids so ``requires`` dependencies run first."""
    by_id = catalog_entry_by_id()
    unknown = [iid for iid in indicator_ids if iid not in by_id]
    if unknown:
        msg = f'Unknown indicator ids: {unknown}'
        raise IndicatorCatalogError(msg)

    ordered: list[str] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(iid: str) -> None:
        if iid in seen:
            return
        if iid in visiting:
            msg = f'Circular indicator requires involving {iid!r}'
            raise IndicatorCatalogError(msg)
        visiting.add(iid)
        for dep in by_id[iid].requires:
            if dep not in by_id:
                msg = f'Indicator {iid!r} requires unknown id {dep!r}'
                raise IndicatorCatalogError(msg)
            visit(dep)
        visiting.remove(iid)
        seen.add(iid)
        ordered.append(iid)

    for iid in indicator_ids:
        visit(iid)
    return tuple(ordered)
