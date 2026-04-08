"""Trend exhaustion reversal after compression and a bullish trigger.

A setup is **not** returned unless **every** step below passes for some trigger bar
(including compression, trigger shape, reversal pair, and EMA context).

Scan order (RTH 2-min ``bar_series.bars_2min``): **candidate trigger indices are tried from the
last bar backward**; the **first** full match is the **most recent** qualifying setup. For each
candidate, **identify the compression segment first**; only if those three bars pass does the
**next** bar qualify as a trigger candidate:

1. **Compression (three consecutive bars)**: each volume must rank in the bottom
   ``VOLUME_PERCENTILE_MAX`` of the prior ``VOLUME_MA_PERIOD`` volumes. If not, skip — do not evaluate
   trigger rules for that position.
2. **Trigger bar** (the bar **after** that block): bullish; close in top of range; close above prior
   bar close.
3. **Reversal bar pair**: first valid bearish→bullish pair in the window from the **highest-volume**
   bar (after trend change, within ``MAX_BARS_SINCE_LAST_TREND_CHANGE``) forward to the candidate
   trigger bar (pair may end on the trigger bar). Same ATR / range thresholds as
   ``_reversal_bar_long_pair``. Candidate trigger index must fall in the last
   ``REVERSAL_BAR_LOOKBACK_BARS`` 2-min bars.
4. **Context**: last EMA9/EMA21 trend change through the bar before trigger. First EMA9 upturn
   index after that cross is **reported** on the setup but **does not** gate the pattern.

**Long** ATR travel reported on each setup: trend-change bar **high** minus minimum **low**
after the trend-change bar through the trigger, divided by daily ATR. Bars since cross are
**reported** (same thresholds as filter constants) but **do not** disqualify a setup. A short
analogue would use trend-change **low** minus maximum **high** over the same segment.
"""

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Literal
from typing import Protocol

from strategies.indicators.atr import atr
from strategies.indicators.ema import ema
from strategies.indicators.vwap import vwap
from trading.models import BarSeries

EMA_FAST_PERIOD = 9
EMA_SLOW_PERIOD = 21
VOLUME_MA_PERIOD = 20
COMPRESSION_BARS = 3
VOLUME_COMPRESSION_MIN_BARS = COMPRESSION_BARS
VOLUME_COMPRESSION_MAX_BARS = COMPRESSION_BARS
VOLUME_PERCENTILE_MAX = 35.0  # each compression bar volume in bottom 30% vs MA history
TRIGGER_CLOSE_LOCATION_MIN = 0.7  # 0.7 = 70% of the bar range
ATR_PERIOD = 14
# Minimum ATRs trigger close is above swing low (min low after trend-change bar through trigger).
MIN_TRAVEL_FROM_LAST_TREND_CHANGE_ATR = 0.5
MAX_BARS_SINCE_LAST_TREND_CHANGE = 50
MIN_BARS_2MIN = 50
# Prior bearish range must be >= this multiple of **daily** ATR (``bars_1d``), not 2-min ATR.
REVERSAL_BAR_MIN_PRIOR_RANGE_ATR = 0.15
REVERSAL_BAR_MIN_TRIGGER_RANGE_FRAC = 0.8
REVERSAL_BAR_LOOKBACK_BARS = 35


class _BarHlcv(Protocol):
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class TrendExhaustionSetup:
    """One detected long reversal setup.

    ``travel_from_trend_change_atr`` is ``(trend_change_high - segment_low) / ATR`` (long), where
    ``segment_low`` is the minimum low after the trend-change bar through the trigger bar.
    ``ema9_first_upturn_bar_index`` is diagnostic; ``None`` if no upturn found through context.
    """

    trigger_bar_time: datetime
    trigger_bar_index: int
    trigger_price: float
    prior_close: float
    trigger_close_location: float
    vwap_at_trigger: float | None
    ema9_at_trigger: float
    ema21_at_trigger: float
    ema9_first_upturn_bar_index: int | None
    compression_bars: int
    compression_volume_percentiles: tuple[float, ...]
    compression_median_percentile: float
    compression_percentile_max: float
    atr_value: float | None
    last_trend_change_price: float
    last_trend_change_direction: Literal['21_below_9', '21_above_9']
    bars_since_last_trend_change: int
    travel_from_trend_change_atr: float
    reversal_prior_range_atr: float
    reversal_trigger_range_vs_prior: float


@dataclass(frozen=True)
class TrendExhaustionScanResult:
    """Scan output for trend exhaustion reversal setup.

    ``first_setup`` is the **most recent** qualifying setup (search runs backward from the last bar).
    ``setups`` contains at most that one setup.
    """

    triggered: bool
    first_setup: TrendExhaustionSetup | None
    setups: tuple[TrendExhaustionSetup, ...]


@dataclass(frozen=True)
class TrendExhaustionLastBarProbe:
    """Compression and trigger-bar checks on the last 2-min bar using the same rules as the scan (no EMA context)."""

    compression_ok: bool
    compression_median: float
    compression_percentile_max: float
    trigger_close_location: float | None
    trigger_ok: bool


@dataclass(frozen=True)
class TrendExhaustionReversalBarProbe:
    """Bearish-then-bullish reversal pair found inside the HV→session-trigger window (diagnostics).

    ``high_volume_bar_index`` anchors the start of the search (max volume after trend change,
    within ``max_bars_since_last_trend_change``, not after the bar before the session trigger).
    The first forward ``_reversal_bar_long_pair`` in ``[hv, session_trigger)`` is reported.
    """

    prior_bearish: bool
    trigger_bullish: bool
    prior_range_atr: float | None
    prior_range_meets_atr: bool
    trigger_range_vs_prior: float | None
    trigger_range_meets_prior_frac: bool
    trigger_in_lookback: bool
    high_volume_bar_index: int | None
    prior_bar_index: int
    reversal_pair_bull_bar_index: int | None
    reversal_bar_ok: bool


@dataclass(frozen=True)
class TrendExhaustionContextSnapshot:
    """Latest trend-change/ATR travel context for diagnostics.

    ``travel_from_trend_change_atr`` (long) is ``(trend_change_high - segment_low) / ATR``,
    with ``segment_low`` the min low after the trend-change bar through the session trigger
    (last bar). ``travel_passes`` is True when that value is at least
    ``min_travel_from_last_trend_change_atr``.
    """

    atr_value: float | None
    last_trend_change_price: float | None
    last_trend_change_direction: Literal['21_below_9', '21_above_9'] | None
    bars_since_last_trend_change: int | None
    travel_from_trend_change_atr: float | None
    bars_since_passes: bool
    travel_passes: bool


def _ema_at(
    bar_series: BarSeries,
    bars_slice: list[_BarHlcv],
    period: int,
) -> float | None:
    if not bars_slice:
        return None
    series = BarSeries(bars_1d=bar_series.bars_1d, bars_2min=bars_slice)
    return ema(series, period)


def _vwap_at(bar_series: BarSeries, bars_slice: list[_BarHlcv]) -> float | None:
    if not bars_slice:
        return None
    series = BarSeries(bars_1d=bar_series.bars_1d, bars_2min=bars_slice)
    return vwap(series)


def _close_location(bar: _BarHlcv) -> float | None:
    bar_high = float(bar.high)
    bar_low = float(bar.low)
    bar_close = float(bar.close)
    bar_range = bar_high - bar_low
    if bar_range <= 0:
        return None
    return (bar_close - bar_low) / bar_range


def _bar_range_hl(bar: _BarHlcv) -> float:
    return float(bar.high) - float(bar.low)


def _reversal_bar_long_pair(
    prior: _BarHlcv,
    trigger: _BarHlcv,
    atr_value: float,
    *,
    min_prior_range_atr: float,
    min_trigger_range_frac: float,
) -> tuple[bool, float, float]:
    """Long setup: bearish prior, bullish trigger, prior range vs **daily** ATR, trigger range vs prior.

    ``atr_value`` must be daily ATR from ``atr(bar_series.bars_1d, ...)``, not intraday 2-min ATR.
    """
    prior_rng = _bar_range_hl(prior)
    trigger_rng = _bar_range_hl(trigger)
    prior_bear = float(prior.close) < float(prior.open)
    trig_bull = float(trigger.close) > float(trigger.open)
    prior_atr = prior_rng / atr_value
    vs_prior = trigger_rng / prior_rng if prior_rng > 0 else 0.0
    ok = (
        prior_bear
        and trig_bull
        and prior_atr >= min_prior_range_atr
        and prior_rng > 0
        and trigger_rng >= min_trigger_range_frac * prior_rng
    )
    return (ok, prior_atr, vs_prior)


def _min_low_since_trend_change(
    bars: list[_BarHlcv],
    trend_change_idx: int,
    end_idx_inclusive: int,
) -> float:
    """Minimum ``low`` over bars **since** the trend-change bar through ``end_idx_inclusive``.

    Excludes the trend-change bar when there are later bars; if the end index is the
    trend-change bar, uses that bar's low only.
    """
    first_idx = trend_change_idx + 1 if trend_change_idx < end_idx_inclusive else trend_change_idx
    return min(float(bars[i].low) for i in range(first_idx, end_idx_inclusive + 1))


def _travel_from_trend_change_atr_long(
    bars: list[_BarHlcv],
    trend_change_idx: int,
    trigger_idx: int,
    daily_atr: float,
) -> float:
    """Long: (trend-change bar high − min low after TC through trigger) / daily ATR."""
    segment_low = _min_low_since_trend_change(bars, trend_change_idx, trigger_idx)
    tc_high = float(bars[trend_change_idx].high)
    return (tc_high - segment_low) / float(daily_atr)


def _sma(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _volume_percentile(value: float, universe: list[float]) -> float | None:
    if not universe:
        return None
    below_or_equal = sum(1 for entry in universe if entry <= value)
    return (below_or_equal / len(universe)) * 100.0


def _compression_pass_percentile(
    bar_volumes: list[float],
    volume_history: list[float],
    percentile_max: float,
) -> tuple[bool, tuple[float, ...], float]:
    if not volume_history:
        return (False, (), 0.0)
    percentiles: list[float] = []
    for current_volume in bar_volumes:
        pct = _volume_percentile(current_volume, volume_history)
        if pct is None:
            return (False, (), 0.0)
        percentiles.append(pct)
    pct_tuple = tuple(percentiles)
    passes = all(pct <= percentile_max for pct in pct_tuple)
    med = median(pct_tuple) if pct_tuple else 0.0
    return (passes, pct_tuple, med)


def _volume_compression_block_ok(
    bars: list[_BarHlcv],
    end_compression_bar_index: int,
    *,
    volume_ma_period: int,
    compression_bar_count: int,
    volume_percentile_max: float,
) -> tuple[bool, tuple[float, ...], float, float]:
    """Volume gate for ``compression_bar_count`` consecutive bars ending at ``end_compression_bar_index``."""
    c_start = end_compression_bar_index - (compression_bar_count - 1)
    if c_start <= 0:
        return (False, (), 0.0, 0.0)
    hist_start = c_start - volume_ma_period
    if hist_start < 0:
        return (False, (), 0.0, 0.0)
    compression_bars_slice = bars[c_start: end_compression_bar_index + 1]
    if len(compression_bars_slice) != compression_bar_count:
        return (False, (), 0.0, 0.0)
    compression_volumes = [float(bar.volume) for bar in compression_bars_slice]
    volume_history = [float(bar.volume) for bar in bars[hist_start:c_start]]
    if _sma(volume_history) is None:
        return (False, (), 0.0, 0.0)
    compression_ok, pct_tuple, med = _compression_pass_percentile(
        compression_volumes,
        volume_history,
        volume_percentile_max,
    )
    pct_max = max(pct_tuple) if pct_tuple else 0.0
    return (compression_ok, pct_tuple, med, pct_max)


def trend_exhaustion_most_recent_volume_compression_end_index(
    bar_series: BarSeries,
    *,
    volume_ma_period: int = VOLUME_MA_PERIOD,
    compression_bar_count: int = COMPRESSION_BARS,
    volume_percentile_max: float = VOLUME_PERCENTILE_MAX,
) -> int | None:
    """Last bar index of the **most recent** ``compression_bar_count`` run that passes the volume gate.

    Walks backward from the final 2-min bar using the same volume logic as
    :func:`trend_exhaustion_reversal_scan` (history is the ``volume_ma_period`` bars
    before the first bar of the run). Returns ``None`` if no run passes.
    """
    bars = bar_series.bars_2min
    min_end_c = volume_ma_period + compression_bar_count - 1
    if len(bars) <= min_end_c:
        return None
    for end_c in range(len(bars) - 1, min_end_c - 1, -1):
        ok, _, _, _ = _volume_compression_block_ok(
            bars,
            end_c,
            volume_ma_period=volume_ma_period,
            compression_bar_count=compression_bar_count,
            volume_percentile_max=volume_percentile_max,
        )
        if ok:
            return end_c
    return None


def _last_trend_change(
    bar_series: BarSeries,
    bars: list[_BarHlcv],
    end_idx: int,
) -> tuple[int, float, Literal['21_below_9', '21_above_9']] | None:
    """Last EMA trend-change price up to ``end_idx``.

    Trend-change is where EMA ordering flips:
    - ``21_below_9`` when EMA21 < EMA9 (bullish stack)
    - ``21_above_9`` when EMA21 > EMA9 (bearish stack)
    Price is the bar close where the flip is first observed.
    """
    if end_idx < 2:
        return None
    prev_relation: Literal['21_below_9', '21_above_9'] | None = None
    last_change: tuple[int, float, Literal['21_below_9', '21_above_9']] | None = None
    for idx in range(1, end_idx + 1):
        bars_slice = bars[: idx + 1]
        ema9_value = _ema_at(bar_series, bars_slice, EMA_FAST_PERIOD)
        ema21_value = _ema_at(bar_series, bars_slice, EMA_SLOW_PERIOD)
        if ema9_value is None or ema21_value is None or ema9_value == ema21_value:
            continue
        relation: Literal['21_below_9', '21_above_9']
        if ema21_value < ema9_value:
            relation = '21_below_9'
        else:
            relation = '21_above_9'
        if prev_relation is None:
            prev_relation = relation
            continue
        if relation != prev_relation:
            last_change = (idx, float(bars[idx].close), relation)
            prev_relation = relation
    return last_change


def _first_ema9_upturn_idx_since(
    bar_series: BarSeries,
    bars: list[_BarHlcv],
    trend_change_idx: int,
    last_idx: int,
) -> int | None:
    """Smallest ``j`` with ``trend_change_idx < j <= last_idx`` where EMA9 rises bar-to-bar."""
    for j in range(trend_change_idx + 1, last_idx + 1):
        if j < 1:
            continue
        em9_now = _ema_at(bar_series, bars[: j + 1], EMA_FAST_PERIOD)
        em9_prev_bar = _ema_at(bar_series, bars[:j], EMA_FAST_PERIOD)
        if em9_now is None or em9_prev_bar is None:
            continue
        if em9_now > em9_prev_bar:
            return j
    return None


def _highest_volume_bar_index_after_trend_change(
    bars: list[_BarHlcv],
    trend_change_idx: int,
    *,
    max_bars_since_trend_change: int,
    last_index_inclusive: int,
) -> int | None:
    """Bar index with largest volume in ``(trend_change_idx, min(last_index_inclusive, trend_change_idx + max)]``."""
    lo = trend_change_idx + 1
    hi = min(last_index_inclusive, trend_change_idx + max_bars_since_trend_change)
    if lo > hi or lo >= len(bars):
        return None
    hi = min(hi, len(bars) - 1)
    best_i = lo
    best_v = float(bars[lo].volume)
    for i in range(lo + 1, hi + 1):
        v = float(bars[i].volume)
        if v > best_v:
            best_v = v
            best_i = i
    return best_i


def _first_reversal_long_pair_from_hv_to_trigger(
    bars: list[_BarHlcv],
    hv_idx: int | None,
    trigger_idx: int,
    daily_atr: float,
    *,
    min_prior_range_atr: float,
    min_trigger_range_frac: float,
) -> tuple[int, float, float] | None:
    """First prior index ``i`` in ``[hv_idx, trigger_idx)`` with a valid long reversal pair."""
    if hv_idx is None or hv_idx > trigger_idx - 1:
        return None
    for i in range(hv_idx, trigger_idx):
        rev_ok, pa, vp = _reversal_bar_long_pair(
            bars[i],
            bars[i + 1],
            daily_atr,
            min_prior_range_atr=min_prior_range_atr,
            min_trigger_range_frac=min_trigger_range_frac,
        )
        if rev_ok:
            return (i, pa, vp)
    return None


def trend_exhaustion_probe_last_bar(
    bar_series: BarSeries,
    *,
    volume_ma_period: int = VOLUME_MA_PERIOD,
    compression_bar_count: int = COMPRESSION_BARS,
    volume_percentile_max: float = VOLUME_PERCENTILE_MAX,
    trigger_close_location_min: float = TRIGGER_CLOSE_LOCATION_MIN,
) -> TrendExhaustionLastBarProbe:
    """Evaluate compression (last ``compression_bar_count`` bars before final bar) and trigger shape on the final bar."""
    bars = bar_series.bars_2min
    trigger_close_location: float | None = None
    trigger_ok = False
    if len(bars) >= 2:
        trigger_bar = bars[-1]
        prior_bar = bars[-2]
        trigger_close_location = _close_location(trigger_bar)
        bullish = float(trigger_bar.close) > float(trigger_bar.open)
        location_ok = (
            trigger_close_location is not None
            and trigger_close_location >= trigger_close_location_min
        )
        above_prior = float(trigger_bar.close) > float(prior_bar.close)
        trigger_ok = bullish and location_ok and above_prior

    compression_ok = False
    compression_median = 0.0
    compression_percentile_max = 0.0
    if len(bars) >= compression_bar_count + 1:
        end_c = len(bars) - 2
        compression_ok, _, med, compression_percentile_max = _volume_compression_block_ok(
            bars,
            end_c,
            volume_ma_period=volume_ma_period,
            compression_bar_count=compression_bar_count,
            volume_percentile_max=volume_percentile_max,
        )
        compression_median = med

    return TrendExhaustionLastBarProbe(
        compression_ok=compression_ok,
        compression_median=round(compression_median, 4),
        compression_percentile_max=round(compression_percentile_max, 2),
        trigger_close_location=(
            round(trigger_close_location, 4) if trigger_close_location is not None else None
        ),
        trigger_ok=trigger_ok,
    )


def trend_exhaustion_reversal_bar_probe_last(
    bar_series: BarSeries,
    *,
    atr_period: int = ATR_PERIOD,
    lookback_bars: int = REVERSAL_BAR_LOOKBACK_BARS,
    max_bars_since_last_trend_change: int = MAX_BARS_SINCE_LAST_TREND_CHANGE,
    min_prior_range_atr: float = REVERSAL_BAR_MIN_PRIOR_RANGE_ATR,
    min_trigger_range_frac: float = REVERSAL_BAR_MIN_TRIGGER_RANGE_FRAC,
) -> TrendExhaustionReversalBarProbe:
    """First reversal pair in the window from HV (post trend change) forward to the session trigger.

    Session trigger is the **final** 2-min bar. Walks forward from ``high_volume_bar_index``;
    each step tests ``_reversal_bar_long_pair(bars[i], bars[i+1])`` until one passes or the
    window ends (bullish leg may be **before** the session trigger). Uses **daily** ATR.
    """
    bars = bar_series.bars_2min
    if len(bars) < 2:
        return TrendExhaustionReversalBarProbe(
            prior_bearish=False,
            trigger_bullish=False,
            prior_range_atr=None,
            prior_range_meets_atr=False,
            trigger_range_vs_prior=None,
            trigger_range_meets_prior_frac=False,
            trigger_in_lookback=False,
            high_volume_bar_index=None,
            prior_bar_index=-1,
            reversal_pair_bull_bar_index=None,
            reversal_bar_ok=False,
        )
    trigger_idx = len(bars) - 1
    session_trigger_in_lookback = trigger_idx >= len(bars) - lookback_bars
    context_idx = trigger_idx - 1
    trend_change = _last_trend_change(bar_series, bars, context_idx)
    hv_idx: int | None = None
    if trend_change is not None:
        trend_change_idx = trend_change[0]
        hv_idx = _highest_volume_bar_index_after_trend_change(
            bars,
            trend_change_idx,
            max_bars_since_trend_change=max_bars_since_last_trend_change,
            last_index_inclusive=trigger_idx - 1,
        )
    atr_value = atr(bar_series.bars_1d, period=atr_period)
    if atr_value is None or atr_value <= 0:
        return TrendExhaustionReversalBarProbe(
            prior_bearish=False,
            trigger_bullish=False,
            prior_range_atr=None,
            prior_range_meets_atr=False,
            trigger_range_vs_prior=None,
            trigger_range_meets_prior_frac=False,
            trigger_in_lookback=session_trigger_in_lookback,
            high_volume_bar_index=hv_idx,
            prior_bar_index=-1,
            reversal_pair_bull_bar_index=None,
            reversal_bar_ok=False,
        )
    found = _first_reversal_long_pair_from_hv_to_trigger(
        bars,
        hv_idx,
        trigger_idx,
        float(atr_value),
        min_prior_range_atr=min_prior_range_atr,
        min_trigger_range_frac=min_trigger_range_frac,
    )
    if found is None:
        return TrendExhaustionReversalBarProbe(
            prior_bearish=False,
            trigger_bullish=False,
            prior_range_atr=None,
            prior_range_meets_atr=False,
            trigger_range_vs_prior=None,
            trigger_range_meets_prior_frac=False,
            trigger_in_lookback=session_trigger_in_lookback,
            high_volume_bar_index=hv_idx,
            prior_bar_index=-1,
            reversal_pair_bull_bar_index=None,
            reversal_bar_ok=False,
        )
    prior_i, prior_atr, vs_prior = found
    bull_i = prior_i + 1
    prior_bar = bars[prior_i]
    bull_bar = bars[bull_i]
    return TrendExhaustionReversalBarProbe(
        prior_bearish=float(prior_bar.close) < float(prior_bar.open),
        trigger_bullish=float(bull_bar.close) > float(bull_bar.open),
        prior_range_atr=round(prior_atr, 4),
        prior_range_meets_atr=prior_atr >= min_prior_range_atr,
        trigger_range_vs_prior=round(vs_prior, 4),
        trigger_range_meets_prior_frac=vs_prior >= min_trigger_range_frac,
        trigger_in_lookback=session_trigger_in_lookback,
        high_volume_bar_index=hv_idx,
        prior_bar_index=prior_i,
        reversal_pair_bull_bar_index=bull_i,
        reversal_bar_ok=session_trigger_in_lookback,
    )


def trend_exhaustion_reversal_scan(
    bar_series: BarSeries,
    *,
    volume_ma_period: int = VOLUME_MA_PERIOD,
    compression_bar_count: int = COMPRESSION_BARS,
    volume_percentile_max: float = VOLUME_PERCENTILE_MAX,
    trigger_close_location_min: float = TRIGGER_CLOSE_LOCATION_MIN,
    atr_period: int = ATR_PERIOD,
    reversal_bar_lookback_bars: int = REVERSAL_BAR_LOOKBACK_BARS,
    max_bars_since_last_trend_change: int = MAX_BARS_SINCE_LAST_TREND_CHANGE,
    reversal_min_prior_range_atr: float = REVERSAL_BAR_MIN_PRIOR_RANGE_ATR,
    reversal_min_trigger_range_frac: float = REVERSAL_BAR_MIN_TRIGGER_RANGE_FRAC,
) -> TrendExhaustionScanResult:
    """Scan backward from the last bar for the most recent valid setup (compression, then trigger, etc.)."""
    bars = bar_series.bars_2min
    if len(bars) < MIN_BARS_2MIN:
        return TrendExhaustionScanResult(triggered=False, first_setup=None, setups=())
    # One daily ATR for the whole scan: reversal-bar gate and travel-from-low use this, not 2-min ATR.
    daily_atr = atr(bar_series.bars_1d, period=atr_period)
    setups: list[TrendExhaustionSetup] = []
    start_idx = max(volume_ma_period + compression_bar_count + 2, EMA_SLOW_PERIOD + 3)
    last_trigger_idx = len(bars) - 1
    for idx in range(last_trigger_idx, start_idx - 1, -1):
        end_c = idx - 1
        compression_ok, percentiles, med, percentile_max = _volume_compression_block_ok(
            bars,
            end_c,
            volume_ma_period=volume_ma_period,
            compression_bar_count=compression_bar_count,
            volume_percentile_max=volume_percentile_max,
        )
        if not compression_ok:
            continue

        trigger_bar = bars[idx]
        prior_bar = bars[idx - 1]
        if float(trigger_bar.close) <= float(trigger_bar.open):
            continue
        trigger_location = _close_location(trigger_bar)
        if trigger_location is None or trigger_location < trigger_close_location_min:
            continue
        if float(trigger_bar.close) <= float(prior_bar.close):
            continue

        if daily_atr is None or daily_atr <= 0:
            continue
        if idx < len(bars) - reversal_bar_lookback_bars:
            continue

        context_idx = idx - 1
        trend_change = _last_trend_change(bar_series, bars, context_idx)
        if trend_change is None:
            continue
        trend_change_idx, trend_change_price, trend_change_direction = trend_change
        hv_idx = _highest_volume_bar_index_after_trend_change(
            bars,
            trend_change_idx,
            max_bars_since_trend_change=max_bars_since_last_trend_change,
            last_index_inclusive=idx - 1,
        )
        rev_found = _first_reversal_long_pair_from_hv_to_trigger(
            bars,
            hv_idx,
            idx,
            float(daily_atr),
            min_prior_range_atr=reversal_min_prior_range_atr,
            min_trigger_range_frac=reversal_min_trigger_range_frac,
        )
        if rev_found is None:
            continue
        _, rev_prior_atr, rev_ratio = rev_found
        first_upturn_idx = _first_ema9_upturn_idx_since(
            bar_series,
            bars,
            trend_change_idx,
            context_idx,
        )

        bars_through_ctx = bars[: context_idx + 1]
        ema9_value = _ema_at(bar_series, bars_through_ctx, EMA_FAST_PERIOD)
        ema21_value = _ema_at(bar_series, bars_through_ctx, EMA_SLOW_PERIOD)
        vwap_value = _vwap_at(bar_series, bars_through_ctx)
        if ema9_value is None or ema21_value is None:
            continue

        bars_since_trend_change = idx - trend_change_idx
        travel_from_change_atr = _travel_from_trend_change_atr_long(
            bars,
            trend_change_idx,
            idx,
            float(daily_atr),
        )

        setup = TrendExhaustionSetup(
            trigger_bar_time=trigger_bar.date,
            trigger_bar_index=idx,
            trigger_price=round(float(trigger_bar.close), 2),
            prior_close=round(float(prior_bar.close), 2),
            trigger_close_location=round(trigger_location, 4),
            vwap_at_trigger=round(vwap_value, 2) if vwap_value is not None else None,
            ema9_at_trigger=round(ema9_value, 2),
            ema21_at_trigger=round(ema21_value, 2),
            ema9_first_upturn_bar_index=first_upturn_idx,
            compression_bars=compression_bar_count,
            compression_volume_percentiles=tuple(round(x, 4) for x in percentiles),
            compression_median_percentile=round(med, 4),
            compression_percentile_max=round(percentile_max, 2),
            atr_value=round(float(daily_atr), 4),
            last_trend_change_price=round(trend_change_price, 2),
            last_trend_change_direction=trend_change_direction,
            bars_since_last_trend_change=bars_since_trend_change,
            travel_from_trend_change_atr=round(travel_from_change_atr, 4),
            reversal_prior_range_atr=round(rev_prior_atr, 4),
            reversal_trigger_range_vs_prior=round(rev_ratio, 4),
        )
        setups.append(setup)
        break

    first_setup = setups[0] if setups else None
    return TrendExhaustionScanResult(
        triggered=first_setup is not None,
        first_setup=first_setup,
        setups=tuple(setups),
    )


def trend_exhaustion_context_snapshot(
    bar_series: BarSeries,
    *,
    min_travel_from_last_trend_change_atr: float = MIN_TRAVEL_FROM_LAST_TREND_CHANGE_ATR,
    max_bars_since_last_trend_change: int = MAX_BARS_SINCE_LAST_TREND_CHANGE,
    atr_period: int = ATR_PERIOD,
) -> TrendExhaustionContextSnapshot:
    """Debug snapshot: TC-high to segment-low ATR travel and bars-since-trend-change gates."""
    bars = bar_series.bars_2min
    if len(bars) < 3:
        return TrendExhaustionContextSnapshot(
            atr_value=None,
            last_trend_change_price=None,
            last_trend_change_direction=None,
            bars_since_last_trend_change=None,
            travel_from_trend_change_atr=None,
            bars_since_passes=False,
            travel_passes=False,
        )

    atr_value = atr(bar_series.bars_1d, period=atr_period)
    trend_change = _last_trend_change(bar_series, bars, len(bars) - 2)
    if atr_value is None or atr_value <= 0 or trend_change is None:
        return TrendExhaustionContextSnapshot(
            atr_value=round(atr_value, 4) if atr_value is not None else None,
            last_trend_change_price=None,
            last_trend_change_direction=None,
            bars_since_last_trend_change=None,
            travel_from_trend_change_atr=None,
            bars_since_passes=False,
            travel_passes=False,
        )

    trend_change_idx, trend_change_price, trend_change_direction = trend_change
    trigger_idx = len(bars) - 1
    bars_since_trend_change = trigger_idx - trend_change_idx
    travel_from_change_atr = _travel_from_trend_change_atr_long(
        bars,
        trend_change_idx,
        trigger_idx,
        float(atr_value),
    )
    bars_since_passes = bars_since_trend_change <= max_bars_since_last_trend_change
    travel_passes = travel_from_change_atr >= min_travel_from_last_trend_change_atr
    return TrendExhaustionContextSnapshot(
        atr_value=round(atr_value, 4),
        last_trend_change_price=round(trend_change_price, 2),
        last_trend_change_direction=trend_change_direction,
        bars_since_last_trend_change=bars_since_trend_change,
        travel_from_trend_change_atr=round(travel_from_change_atr, 4),
        bars_since_passes=bars_since_passes,
        travel_passes=travel_passes,
    )


def trend_exhaustion_reversal(
    bar_series: BarSeries,
    **kw,
) -> bool:
    """True when any trend exhaustion long reversal setup is present."""
    return trend_exhaustion_reversal_scan(bar_series, **kw).triggered
