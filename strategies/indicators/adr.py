"""Average Daily Range (ADR) over a configurable number of days."""

from typing import TYPE_CHECKING

from trading.bar_loader import get_bars

DEFAULT_ADR_DAYS = 20

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
    return round(sum(ranges) / len(ranges), 2) if ranges else None
