"""Average Daily Range (ADR) over a configurable number of days."""

from typing import TYPE_CHECKING

import pandas as pd

from trading.bar_loader import get_bars

INDICATOR_DECIMAL_PLACES = 2
DEFAULT_ADR_DAYS = 14

if TYPE_CHECKING:
    from ib_async import IB

    from trading.models import BarSeries


def calculate_adr(
    ib: 'IB | None',
    symbol: str,
    days: int = DEFAULT_ADR_DAYS,
    bundle: 'BarSeries | None' = None,
) -> float | None:
    """Average Daily Range over specified days."""
    if bundle is not None:
        bars = bundle.bars_1d[-days:] if len(bundle.bars_1d) >= days else bundle.bars_1d
    else:
        bars = get_bars(ib, symbol, duration_str=f'{days} D', bar_size='1 day')
    if bars is None:
        return None
    ranges = [b.high - b.low for b in bars]
    return round(sum(ranges) / len(ranges), INDICATOR_DECIMAL_PLACES) if ranges else None


def adr_series(
    trading_date: pd.Series,
    daily_bars: pd.DataFrame,
    days: int = DEFAULT_ADR_DAYS,
) -> pd.Series:
    """Map ADR (mean of prior ``days`` RTH daily ranges) onto each bar by ``trading_date``.

    Uses completed prior session days only (excludes the current day's range from the mean).
    """
    if daily_bars.empty:
        return pd.Series([float('nan')] * len(trading_date), index=trading_date.index)

    daily = daily_bars.sort_values('trading_date').copy()
    daily['range'] = daily.high.astype('float64') - daily.low.astype('float64')
    daily['adr'] = (
        daily['range']
        .shift(1)
        .rolling(window=days, min_periods=days)
        .mean()
        .round(INDICATOR_DECIMAL_PLACES)
    )
    adr_lookup: dict[object, float] = {
        date_key: float(value)
        for date_key, value in daily.set_index('trading_date').adr.items()
        if pd.notna(value)
    }
    return trading_date.map(adr_lookup).astype('float64')
