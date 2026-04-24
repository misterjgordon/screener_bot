"""Breakout bar pattern checks used by strategy setup scans."""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING


from trading.market_data import get_realtime_bar
from trading.models import Bar
from trading.models import TickerQuote

if TYPE_CHECKING:
    from ib_async import RealTimeBar
    from ib_async import IB

    from trading.models import BarSeries

BREAK_OUT_LOOKBACK_BARS = 5  # max 195 bars
BREAK_OUT_SIZE_MULTIPLIER = 3.0


def breakout_limit_entry_price(
    midpoint_of_breakout_bar: float,
    is_long: bool,
    quote: TickerQuote | None,
) -> float:
    """Limit price when a breakout bar fired: anchor to quote so the limit is fillable.

    Long: min(midpoint, ask) when ask is valid; else midpoint.
    Short: max(midpoint, bid) when bid is valid; else midpoint.

    Caller supplies ``quote`` from a single ``get_ticker_quote`` (e.g. entry_mode); this
    function does not request market data.
    """
    mid = float(midpoint_of_breakout_bar)
    if is_long:
        ask = quote.ask if quote is not None else None
        if ask is not None and ask > 0:
            return min(mid, float(ask))
        return mid
    bid = quote.bid if quote is not None else None
    if bid is not None and bid > 0:
        return max(mid, float(bid))
    return mid


@dataclass
class BreakOutBarStats:
    """Result of break_out_bar pattern scan on 2-min bars."""

    breakout: bool
    largest_bar_size: float | None
    avg_bar_size: float | None
    midpoint_of_breakout_bar: float | None
    breakout_bar_bullish: bool | None  # True = long, False = short, None when no breakout


def _bar_size(bar: Bar) -> float:
    """Return full bar range (high-low)."""
    return bar.high - bar.low


def _realtime_bar_ohlc(rt_bar: 'RealTimeBar') -> tuple[float, float, float, float, datetime]:
    """OHLC and bar time from an ib_async :class:`~ib_async.objects.RealTimeBar`."""
    return (
        float(rt_bar.open_),
        float(rt_bar.high),
        float(rt_bar.low),
        float(rt_bar.close),
        rt_bar.time,
    )


def _bar_high_low(bar: Bar) -> tuple[float, float]:
    """High and low from a :class:`~trading.models.Bar`."""
    return (bar.high, bar.low)


def _synthetic_bar(last_2min_bar: Bar, rt_bar: 'RealTimeBar') -> Bar:
    """Build bar: high = max(last 2-min high, realtime high), low = min(last 2-min low, realtime low); open/close from 5-sec."""
    o, h_rt, l_rt, c, dt = _realtime_bar_ohlc(rt_bar)
    h_2min, low_2min = _bar_high_low(last_2min_bar)
    high = max(h_2min, h_rt)
    low = min(low_2min, l_rt)
    return Bar(
        date=dt,
        open=o,
        high=high,
        low=low,
        close=c,
        volume=float(rt_bar.volume),
    )


def break_out_bar_stats(
    bar_series: 'BarSeries',
    lookback_bars: int = BREAK_OUT_LOOKBACK_BARS,
    ib: 'IB | None' = None,
    symbol: str | None = None,
) -> BreakOutBarStats:
    """Return breakout pattern result: flag, sizes, and midpoint of largest bar.

    Uses bar_series.bars_2min_rth. When ib and symbol are provided, fetches one 5-sec realtime bar
    and merges it with the last 2-min bar into a synthetic bar (high/low extended) for the lookback
    so the current incomplete 2-min bar is included in breakout detection.
    """
    bars_2min = bar_series.bars_2min_rth
    if lookback_bars <= 0:
        return BreakOutBarStats(False, None, None, None, None)

    bars_for_lookback: list = list(bars_2min)
    if ib is not None and symbol is not None and bars_2min:
        rt_bar = get_realtime_bar(ib, symbol)
        if rt_bar is not None:
            _, h_rt, l_rt, _, _dt = _realtime_bar_ohlc(rt_bar)
            if h_rt >= l_rt:
                synthetic = _synthetic_bar(bars_2min[-1], rt_bar)
                bars_for_lookback = bars_2min + [synthetic]

    if len(bars_for_lookback) < lookback_bars:
        return BreakOutBarStats(False, None, None, None, None)

    bar_sizes = [_bar_size(bar) for bar in bars_2min]
    if not bar_sizes:
        return BreakOutBarStats(False, None, None, None, None)

    avg_size = round(sum(bar_sizes) / len(bar_sizes), 2)
    if avg_size <= 0:
        return BreakOutBarStats(False, None, None, None, None)

    lookback_bars_list = list(bars_for_lookback[-lookback_bars:])
    lookback_sizes = [_bar_size(b) for b in lookback_bars_list]
    largest_lookback_size = max(lookback_sizes) if lookback_sizes else None
    if largest_lookback_size is None:
        return BreakOutBarStats(False, None, avg_size, None, None)

    largest_lookback_size = round(largest_lookback_size, 2)
    largest_bar = max(lookback_bars_list, key=_bar_size)
    midpoint_of_largest_bar = round((largest_bar.high + largest_bar.low) / 2, 2)
    threshold = avg_size * BREAK_OUT_SIZE_MULTIPLIER
    breakout = largest_lookback_size > threshold
    is_bullish = bool(largest_bar.close > largest_bar.open) if breakout else None
    return BreakOutBarStats(
        breakout=breakout,
        largest_bar_size=largest_lookback_size,
        avg_bar_size=avg_size,
        midpoint_of_breakout_bar=midpoint_of_largest_bar if breakout else None,
        breakout_bar_bullish=is_bullish,
    )


def break_out_bar(
    bar_series: 'BarSeries',
    lookback_bars: int = BREAK_OUT_LOOKBACK_BARS,
    ib: 'IB | None' = None,
    symbol: str | None = None,
) -> bool:
    """Check for breakout bar in recent lookback window."""
    return break_out_bar_stats(
        bar_series, lookback_bars=lookback_bars, ib=ib, symbol=symbol
    ).breakout
