"""Rubber band scalp: break of the prior 2-min bar after an extended leg (long / short mirror).

Scan order (RTH 2-min bars):

1. Drop the first and last **two** RTH 2-min bars (no open/close noise); all ranking is over the
   remainder only.
2. Take the **top N** by **range** (high − low) among those bars (``TOP_SIZE_RANK``).
3. Visit those bars **newest → oldest** (most recent first).
4. **Extension vs bar color** (session LOD/HOD through this bar): **Bullish** (close > open) →
   **(RTH open − LOD) / ATR ≥ ``atr_extension_min``**. **Bearish** → **(HOD − RTH open) / ATR ≥ …**.
   **Doji** → skip. LOD/HOD are cumulative through the snap bar index, not that bar’s low/high alone.
5. Optional: if ``min_snap_open_leg_atr > 0``, require the **snap bar** open-to-extreme in the color
   direction (bullish: ``open − low``; bearish: ``high − open``) in ATR units — applied **after** the
   session extension test.
6. **Then** prior-bar break, **RVOL** ≥ ``rvol_min``, snap **range** **>** prior bar range.

Thresholds are overridable on ``rubberband_scan`` (``atr_extension_min``, ``rvol_min``,
``top_sized_bar_count``, ``min_snap_open_leg_atr``, ``diagnose_all_top_bars``).

When the **last loaded 2-min bar is still open**, append a synthetic bar via
``bar_series_with_synthetic_2min_tail`` (see ``last_loaded_2min_bar_is_incomplete``) to fold in a live
``last_price`` vs the stale close.
"""

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import Literal
from typing import Protocol

from strategies.indicators.atr import atr
from strategies.indicators.rvol import rvol
from strategies.indicators.vwap import vwap
from strategies.utils import bar_date
from strategies.utils import is_rth_session_bar
from trading.models import Bar
from trading.models import BarSeries

RVOL_MIN = 2.0
RVOL_PERIOD = 10
ATR_PERIOD = 14
ATR_EXTENSION_MIN = 2.0
STOP_OFFSET = 0.02
PRIOR_BARS_FOR_BREAK = 1
TOP_SIZE_RANK = 5
MIN_SNAP_OPEN_LEG_ATR = 0.0
MIN_BARS_2MIN = 2
# Ignore candidates in opening/closing 2-min bars (mixed signals).
EXCLUDE_EDGE_RTH_2MIN_BARS = 2


class _BarHlcv(Protocol):
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class RubberbandFactors:
    """Gates and context for one candidate bar (after geometry is satisfied).

    ``relative_volume*`` ← ``rvol()``; ``atr_change`` ← ``atr()`` on dailies; ``open_leg_atr`` =
    for **long** setups, session **down** from open in ATR (``(open − LOD) / ATR``); for **short**,
    session **up** from open (``(HOD − open) / ATR``). ``this_bar_range*`` = high−low vs prior bar.
    ``snap_open_leg_atr`` = for **long**, ``(open − low) / ATR`` on the snap bar; for **short**,
    ``(high − open) / ATR`` (optional gate when ``min_snap_open_leg_atr > 0``).

    Match gates (see ``rubberband_match_ok``): ``meets_min_open_leg_atr``,
    ``meets_relative_volume_min``, ``this_bar_range_gt_prior``.
    """

    relative_volume: float | None
    meets_relative_volume_min: bool
    relative_volume_min: float
    atr_change: float | None
    regular_session_open: float | None
    open_leg_atr: float | None
    meets_min_open_leg_atr: bool
    min_open_leg_atr: float
    this_bar_range: float | None
    prior_bar_range: float | None
    this_bar_range_gt_prior: bool
    snap_open_leg_atr: float | None
    min_snap_open_leg_atr: float


@dataclass
class RubberbandSetup:
    """One snap bar that clears the prior 2-min bar (entry reference = that break level; stop per sheet)."""

    direction: Literal['long', 'short']
    snap_bar_time: datetime
    entry_reference: float
    stop_price: float
    low_of_day: float
    high_of_day: float
    vwap: float | None
    factors: RubberbandFactors


@dataclass
class RubberbandMissDiagnosis:
    """One top-N bar the scan considered and why it did not yield a qualifying setup (if at all)."""

    code: str
    snap_bar_time: datetime | None = None
    rth_bar_index: int | None = None
    direction: Literal['long', 'short'] | None = None
    down_from_open_atr: float | None = None
    up_from_open_atr: float | None = None
    detail: str = ''
    factors: RubberbandFactors | None = None
    failed_match_gate_names: tuple[str, ...] = ()


@dataclass
class RubberbandScanResult:
    """Outcome of scanning one symbol's bars for a session.

    ``setup`` is the first match in **newest-first** order among those top-N range-ranked bars.
    ``first_pattern_setup`` is the same reference (API compatibility).

    ``miss_diagnoses`` is set when ``rubberband_scan(..., with_miss_diagnoses=True)`` and either
    there is no qualifying setup, or ``diagnose_all_top_bars=True`` (per-bar rows even when a setup
    exists).
    """

    exists: bool
    session_date: date
    setup: RubberbandSetup | None
    first_pattern_setup: RubberbandSetup | None
    miss_diagnoses: tuple[RubberbandMissDiagnosis, ...] | None = None


def bar_series_with_synthetic_2min_tail(
    bar_series: BarSeries,
    *,
    last_price: float,
    bar_end: datetime,
    volume: float = 0.0,
) -> BarSeries:
    """Return a copy of ``bar_series`` with one synthetic 2-min bar appended.

    Open is the prior bar's close; high/low wrap ``last_price``; close is ``last_price``.
    ``bars_1d`` is unchanged. Use to bridge the last historical print to a current quote.
    """
    prior = list(bar_series.bars_2min)
    if not prior:
        return bar_series
    last_hist = prior[-1]
    open_px = float(last_hist.close)
    px = float(last_price)
    syn = Bar(
        date=bar_end,
        open=open_px,
        high=max(open_px, px),
        low=min(open_px, px),
        close=px,
        volume=volume,
    )
    return BarSeries(bars_1d=bar_series.bars_1d, bars_2min=prior + [syn])


def last_loaded_2min_bar_is_incomplete(
    last_bar: Bar,
    *,
    as_of: datetime | None = None,
) -> bool:
    """True while wall time is still inside the last bar's 2-minute window (bar not fully closed).

    Assumes ``last_bar.date`` is the **start** of the 2-min bucket (IB-style intraday bars).
    Default ``as_of`` is ``datetime.now(tz=bar_start.tzinfo)`` when the bar is timezone-aware, else
    naive ``datetime.now()``, so comparison never mixes naive and aware datetimes. When the session
    in the bundle is already fully history, ``as_of`` is past the bar end and this returns False.
    """
    bar_start = last_bar.date
    if not isinstance(bar_start, datetime):
        return False
    bar_end = bar_start + timedelta(minutes=2)
    if as_of is None:
        ref = datetime.now(bar_start.tzinfo)
    else:
        ref = as_of
    if (ref.tzinfo is None) != (bar_end.tzinfo is None):
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=bar_end.tzinfo)
        else:
            bar_end = bar_end.astimezone(ref.tzinfo)
    return ref < bar_end


def _bar_high_low_vol(bar: _BarHlcv) -> tuple[float, float, float]:
    return (float(bar.high), float(bar.low), float(bar.volume))


def _bar_range(bar: _BarHlcv) -> float:
    hi, lo, _ = _bar_high_low_vol(bar)
    return hi - lo


def _snap_bar_open_leg_atr_ok(bar: _BarHlcv, atr_val: float, min_snap_open_leg_atr: float) -> bool:
    """True if the bar extends from its open by at least ``min_snap_open_leg_atr`` × ATR in color direction.

    Bullish (close > open): ``(open − low) / atr >= min``. Bearish: ``(high − open) / atr >= min``.
    Doji or flat bar → False. When ``min_snap_open_leg_atr <= 0``, always True (caller should skip).
    """
    if min_snap_open_leg_atr <= 0:
        return True
    if atr_val <= 0:
        return False
    o, hi, lo = float(bar.open), float(bar.high), float(bar.low)
    c = float(bar.close)
    if c > o:
        return (o - lo) / atr_val >= min_snap_open_leg_atr
    if c < o:
        return (hi - o) / atr_val >= min_snap_open_leg_atr
    return False


def _rth_bars_for_session(bars_2min: list, session_date: date) -> list:
    return [b for b in bars_2min if is_rth_session_bar(b.date, session_date)]


def _session_open_rth(rth_bars: list[Bar]) -> float | None:
    if not rth_bars:
        return None
    return float(rth_bars[0].open)


def _max_high_prior(bars: list, end_exclusive: int, count: int) -> float | None:
    start = end_exclusive - count
    if start < 0:
        return None
    highs = [float(b.high) for b in bars[start:end_exclusive]]
    return max(highs) if highs else None


def _min_low_prior(bars: list, end_exclusive: int, count: int) -> float | None:
    start = end_exclusive - count
    if start < 0:
        return None
    lows = [float(b.low) for b in bars[start:end_exclusive]]
    return min(lows) if lows else None


def _lod_hod_through(bars: list, end_inclusive: int) -> tuple[float, float]:
    segment = bars[: end_inclusive + 1]
    lod = min(float(b.low) for b in segment)
    hod = max(float(b.high) for b in segment)
    return (lod, hod)


def _directional_extensions_atr(
    lod: float,
    hod: float,
    rth_open: float | None,
    atr_val: float | None,
) -> tuple[float, float] | None:
    """Return ``(down_from_open_atr, up_from_open_atr)``; None if not computable.

    * ``down_from_open_atr`` = ``(open − LOD) / ATR`` — how far the session has stretched **down**
      (bullish rubber-band context).
    * ``up_from_open_atr`` = ``(HOD − open) / ATR`` — how far the session has stretched **up**
      (bearish context).
    """
    if rth_open is None or atr_val is None or atr_val <= 0:
        return None
    down_from_open = (rth_open - lod) / atr_val
    up_from_open = (hod - rth_open) / atr_val
    return (down_from_open, up_from_open)


def _vwap_through(bar_series: BarSeries, bars_slice: list) -> float | None:
    if not bars_slice:
        return None
    sub = BarSeries(bars_1d=bar_series.bars_1d, bars_2min=bars_slice)
    return vwap(sub)


def _rth_inner_window_indices(rth_len: int) -> list[int] | None:
    """RTH bar indices eligible for ranking (first/last N 2-min bars excluded)."""
    lo = max(PRIOR_BARS_FOR_BREAK, EXCLUDE_EDGE_RTH_2MIN_BARS)
    hi_exclusive = rth_len - EXCLUDE_EDGE_RTH_2MIN_BARS
    if hi_exclusive <= lo:
        return None
    return list(range(lo, hi_exclusive))


def _top_size_indices_in_window(rth: list, window_indices: list[int], n: int) -> list[int]:
    """Up to ``n`` indices in ``window_indices`` with largest bar range (ties: larger index wins)."""
    if not window_indices:
        return []
    ranked = sorted(
        window_indices,
        key=lambda i: (_bar_range(rth[i]), i),
        reverse=True,
    )
    k = min(n, len(ranked))
    return ranked[:k]


def _indices_newest_first(rth: list, indices: list[int]) -> list[int]:
    """Sort bar indices by bar time descending (most recent first)."""
    return sorted(indices, key=lambda i: rth[i].date, reverse=True)


def _build_factors(
    bar_series: BarSeries,
    rth: list,
    snap_idx: int,
    direction: Literal['long', 'short'],
    lod: float,
    hod: float,
    rth_open: float | None,
    atr_val: float | None,
    *,
    atr_extension_min: float,
    rvol_min: float,
    min_snap_open_leg_atr: float,
) -> RubberbandFactors:
    """``open_leg_atr`` is session down-from-open for long, up-from-open for short (both in ATR)."""
    daily_rvol = rvol(bar_series, period=RVOL_PERIOD)
    rvol_ok = daily_rvol is not None and daily_rvol >= rvol_min

    extension: float | None = None
    extension_ok = False
    if atr_val is not None and atr_val > 0 and rth_open is not None:
        if direction == 'long':
            extension = (rth_open - lod) / atr_val
        else:
            extension = (hod - rth_open) / atr_val
        extension_ok = extension >= atr_extension_min

    snap_bar = rth[snap_idx]
    snap_range = _bar_range(snap_bar)
    prior_range: float | None = None
    snap_larger = False
    if snap_idx > 0:
        prior_range = _bar_range(rth[snap_idx - 1])
        snap_larger = snap_range > prior_range

    snap_leg: float | None = None
    if atr_val is not None and atr_val > 0:
        so, sh, sl = float(snap_bar.open), float(snap_bar.high), float(snap_bar.low)
        if direction == 'long':
            snap_leg = (so - sl) / atr_val
        else:
            snap_leg = (sh - so) / atr_val

    return RubberbandFactors(
        relative_volume=daily_rvol,
        meets_relative_volume_min=rvol_ok,
        relative_volume_min=rvol_min,
        atr_change=atr_val,
        regular_session_open=rth_open,
        open_leg_atr=round(extension, 4) if extension is not None else None,
        meets_min_open_leg_atr=extension_ok,
        min_open_leg_atr=atr_extension_min,
        this_bar_range=round(snap_range, 4),
        prior_bar_range=round(prior_range, 4) if prior_range is not None else None,
        this_bar_range_gt_prior=snap_larger,
        snap_open_leg_atr=round(snap_leg, 4) if snap_leg is not None else None,
        min_snap_open_leg_atr=min_snap_open_leg_atr,
    )


def rubberband_match_ok(factors: RubberbandFactors) -> bool:
    """True when open-leg ATR, relative volume, and snap bar range vs prior all pass."""
    return (
        factors.meets_min_open_leg_atr
        and factors.meets_relative_volume_min
        and factors.this_bar_range_gt_prior
    )


def rubberband_failed_match_gate_names(factors: RubberbandFactors) -> tuple[str, ...]:
    """Which gate failed: min open-leg ATR, relative volume floor, or snap range vs prior."""
    failed: list[str] = []
    if not factors.meets_min_open_leg_atr:
        failed.append('min_open_leg_atr')
    if not factors.meets_relative_volume_min:
        failed.append('relative_volume_min')
    if not factors.this_bar_range_gt_prior:
        failed.append('snap_bar_wider_than_prior')
    return tuple(failed)


def rubberband_match_summary(factors: RubberbandFactors) -> str:
    """One line: the three gates, whether all pass, and key numbers."""
    ok = rubberband_match_ok(factors)
    return (
        f'match_ok={ok} meets_min_open_leg_atr={factors.meets_min_open_leg_atr} '
        f'(long=down_from_open short=up_from_open >={factors.min_open_leg_atr} ATR) '
        f'meets_relative_volume_min={factors.meets_relative_volume_min} '
        f'(>={factors.relative_volume_min} rvol) '
        f'this_bar_range_gt_prior={factors.this_bar_range_gt_prior} '
        f'| open_leg_atr={factors.open_leg_atr} snap_open_leg_atr={factors.snap_open_leg_atr} '
        f'(min>={factors.min_snap_open_leg_atr} ATR on bar) '
        f'atr_change={factors.atr_change} regular_session_open={factors.regular_session_open} '
        f'relative_volume={factors.relative_volume} this_bar_range={factors.this_bar_range} '
        f'prior_bar_range={factors.prior_bar_range}'
    )


def _scan_rubberband_session(
    bar_series: BarSeries,
    session_date: date,
    *,
    atr_extension_min: float,
    rvol_min: float,
    top_sized_bar_count: int,
    min_snap_open_leg_atr: float,
    diagnose_all_top_bars: bool,
    with_miss_diagnoses: bool,
) -> tuple[
    RubberbandSetup | None,
    RubberbandSetup | None,
    tuple[RubberbandMissDiagnosis, ...],
]:
    """Return (qualifying_setup, first_pattern_setup, miss_rows).

    ``miss_rows`` is empty unless ``with_miss_diagnoses``; when set, explains each top-N bar that
    did not produce a match (newest-first order). Extension from open is **bullish = down** ATRs,
    **bearish = up** ATRs (see module docstring).

    When ``diagnose_all_top_bars`` is True, the scan keeps visiting bars after the first full match
    so misses can list factor pass/fail for every top-range bar that clears the session extension
    step (and optional snap-bar gate).
    """
    misses: list[RubberbandMissDiagnosis] = []
    best_setup: RubberbandSetup | None = None

    def _miss(row: RubberbandMissDiagnosis) -> None:
        if with_miss_diagnoses:
            misses.append(row)

    bars_all = bar_series.bars_2min
    rth = _rth_bars_for_session(bars_all, session_date)
    window_indices = _rth_inner_window_indices(len(rth))
    if window_indices is None or len(rth) < MIN_BARS_2MIN:
        _miss(
            RubberbandMissDiagnosis(
                code='rth_window_too_short',
                detail=f'rth_len={len(rth)} min_required={MIN_BARS_2MIN}',
            ),
        )
        return (None, None, tuple(misses))

    shortlist = _top_size_indices_in_window(rth, window_indices, top_sized_bar_count)
    ordered = _indices_newest_first(rth, shortlist)
    if not ordered:
        _miss(
            RubberbandMissDiagnosis(
                code='empty_shortlist',
                detail='top-N-by-range produced no bar indices',
            ),
        )
        return (None, None, tuple(misses))

    atr_val = atr(bar_series.bars_1d, period=ATR_PERIOD)
    rth_open = _session_open_rth(rth)

    for i in ordered:
        bar = rth[i]
        if not isinstance(bar.date, datetime):
            continue

        bar_t = bar.date
        lod, hod = _lod_hod_through(rth, i)
        ext_pair = _directional_extensions_atr(lod, hod, rth_open, atr_val)
        if ext_pair is None:
            _miss(
                RubberbandMissDiagnosis(
                    code='extension_atr_unavailable',
                    snap_bar_time=bar_t,
                    rth_bar_index=i,
                    detail='rth_open or atr missing or atr<=0',
                ),
            )
            continue
        down_atr, up_atr = ext_pair
        down_r = round(down_atr, 4)
        up_r = round(up_atr, 4)

        hi, lo, _ = _bar_high_low_vol(bar)
        is_green = float(bar.close) > float(bar.open)
        is_red = float(bar.close) < float(bar.open)

        if is_green:
            if down_atr < atr_extension_min:
                _miss(
                    RubberbandMissDiagnosis(
                        code='bullish_bar_session_not_down_enough',
                        snap_bar_time=bar_t,
                        rth_bar_index=i,
                        down_from_open_atr=down_r,
                        up_from_open_atr=up_r,
                        detail=(
                            f'bullish bar needs session down from open >= {atr_extension_min} ATR; '
                            f'down_from_open_atr={down_r} (open−LOD)/ATR'
                        ),
                    ),
                )
                continue
        elif is_red:
            if up_atr < atr_extension_min:
                _miss(
                    RubberbandMissDiagnosis(
                        code='bearish_bar_session_not_up_enough',
                        snap_bar_time=bar_t,
                        rth_bar_index=i,
                        down_from_open_atr=down_r,
                        up_from_open_atr=up_r,
                        detail=(
                            f'bearish bar needs session up from open >= {atr_extension_min} ATR; '
                            f'up_from_open_atr={up_r} (HOD−open)/ATR'
                        ),
                    ),
                )
                continue
        else:
            _miss(
                RubberbandMissDiagnosis(
                    code='neutral_bar',
                    snap_bar_time=bar_t,
                    rth_bar_index=i,
                    down_from_open_atr=down_r,
                    up_from_open_atr=up_r,
                    detail='close==open; neither bullish nor bearish extension rule applies',
                ),
            )
            continue

        implied_direction: Literal['long', 'short'] = 'long' if is_green else 'short'

        if min_snap_open_leg_atr > 0:
            if atr_val is None or atr_val <= 0:
                _miss(
                    RubberbandMissDiagnosis(
                        code='snap_open_leg_atr_unavailable',
                        snap_bar_time=bar_t,
                        rth_bar_index=i,
                        down_from_open_atr=down_r,
                        up_from_open_atr=up_r,
                        detail=(
                            f'min_snap_open_leg_atr={min_snap_open_leg_atr} requires daily ATR '
                            f'(got atr={atr_val})'
                        ),
                    ),
                )
                continue
            if not _snap_bar_open_leg_atr_ok(bar, float(atr_val), min_snap_open_leg_atr):
                _miss(
                    RubberbandMissDiagnosis(
                        code='snap_open_leg_failed',
                        snap_bar_time=bar_t,
                        rth_bar_index=i,
                        down_from_open_atr=down_r,
                        up_from_open_atr=up_r,
                        detail=(
                            f'snap bar open leg < {min_snap_open_leg_atr} ATR in color direction '
                            f'(bullish: open−low; bearish: high−open)'
                        ),
                    ),
                )
                continue

        factors_session = _build_factors(
            bar_series,
            rth,
            i,
            implied_direction,
            lod,
            hod,
            rth_open,
            atr_val,
            atr_extension_min=atr_extension_min,
            rvol_min=rvol_min,
            min_snap_open_leg_atr=min_snap_open_leg_atr,
        )

        prior_max_high = _max_high_prior(rth, i, PRIOR_BARS_FOR_BREAK)
        prior_min_low = _min_low_prior(rth, i, PRIOR_BARS_FOR_BREAK)
        if prior_max_high is None or prior_min_low is None:
            _miss(
                RubberbandMissDiagnosis(
                    code='prior_bar_unavailable',
                    snap_bar_time=bar_t,
                    rth_bar_index=i,
                    down_from_open_atr=down_r,
                    up_from_open_atr=up_r,
                    factors=factors_session,
                    detail='not enough prior bars for break check',
                ),
            )
            continue

        bars_through = rth[: i + 1]
        vwap_val = _vwap_through(bar_series, bars_through)

        directions: list[Literal['long', 'short']] = []
        if is_green and hi > prior_max_high:
            directions.append('long')
        if is_red and lo < prior_min_low:
            directions.append('short')

        if not directions:
            _miss(
                RubberbandMissDiagnosis(
                    code='no_breaking_direction',
                    snap_bar_time=bar_t,
                    rth_bar_index=i,
                    down_from_open_atr=down_r,
                    up_from_open_atr=up_r,
                    factors=factors_session,
                    detail=(
                        f'green={is_green} red={is_red} hi={hi:.4f} prior_high={prior_max_high:.4f} '
                        f'lo={lo:.4f} prior_low={prior_min_low:.4f}'
                    ),
                ),
            )
            continue

        for direction in directions:
            if direction == 'long':
                entry_ref = prior_max_high
                stop_price = round(lod - STOP_OFFSET, 2)
                risk = entry_ref - stop_price
            else:
                entry_ref = prior_min_low
                stop_price = round(hod + STOP_OFFSET, 2)
                risk = stop_price - entry_ref

            if risk <= 0:
                _miss(
                    RubberbandMissDiagnosis(
                        code='risk_non_positive',
                        snap_bar_time=bar_t,
                        rth_bar_index=i,
                        direction=direction,
                        down_from_open_atr=down_r,
                        up_from_open_atr=up_r,
                        factors=factors_session,
                        detail=f'entry_ref={entry_ref} stop={stop_price} risk={risk}',
                    ),
                )
                continue

            if not rubberband_match_ok(factors_session):
                _miss(
                    RubberbandMissDiagnosis(
                        code='match_gates_failed',
                        snap_bar_time=bar_t,
                        rth_bar_index=i,
                        direction=direction,
                        down_from_open_atr=down_r,
                        up_from_open_atr=up_r,
                        factors=factors_session,
                        failed_match_gate_names=rubberband_failed_match_gate_names(factors_session),
                        detail=rubberband_match_summary(factors_session),
                    ),
                )
                continue

            candidate = RubberbandSetup(
                direction=direction,
                snap_bar_time=bar.date,
                entry_reference=round(entry_ref, 2),
                stop_price=stop_price,
                low_of_day=lod,
                high_of_day=hod,
                vwap=vwap_val,
                factors=factors_session,
            )
            if not diagnose_all_top_bars:
                return (candidate, candidate, tuple(misses))
            if best_setup is None:
                best_setup = candidate
            break

    return (best_setup, best_setup, tuple(misses))


def rubberband_scan(
    bar_series: BarSeries,
    session_date: date | None = None,
    *,
    atr_extension_min: float = ATR_EXTENSION_MIN,
    rvol_min: float = RVOL_MIN,
    top_sized_bar_count: int = TOP_SIZE_RANK,
    min_snap_open_leg_atr: float = MIN_SNAP_OPEN_LEG_ATR,
    diagnose_all_top_bars: bool = False,
    with_miss_diagnoses: bool = False,
) -> RubberbandScanResult:
    """Scan one symbol's bars for the session; ``exists`` is True if any setup matches.

    Match = among top-``top_sized_bar_count`` bars by range (after edge trim), newest-first: session
    LOD/HOD extension vs ``atr_extension_min``, optional snap open-leg if ``min_snap_open_leg_atr > 0``,
    then prior-bar break, RVOL, snap-range gates. With ``diagnose_all_top_bars`` and miss rows, every
    such bar that clears session (+ optional snap) yields factor pass/fail rows until all N are seen.
    """
    bars_all = bar_series.bars_2min

    if session_date is not None:
        resolved_session = session_date
    elif bars_all:
        bd = bar_date(bars_all[-1].date)
        resolved_session = bd if bd is not None else date.today()
    else:
        resolved_session = date.today()

    if not bars_all:
        miss: tuple[RubberbandMissDiagnosis, ...] | None = None
        if with_miss_diagnoses:
            miss = (
                RubberbandMissDiagnosis(
                    code='no_2min_bars',
                    detail='bar_series.bars_2min empty',
                ),
            )
        return RubberbandScanResult(
            exists=False,
            session_date=resolved_session,
            setup=None,
            first_pattern_setup=None,
            miss_diagnoses=miss,
        )

    setup, first_pattern, raw_misses = _scan_rubberband_session(
        bar_series,
        resolved_session,
        atr_extension_min=atr_extension_min,
        rvol_min=rvol_min,
        top_sized_bar_count=top_sized_bar_count,
        min_snap_open_leg_atr=min_snap_open_leg_atr,
        diagnose_all_top_bars=diagnose_all_top_bars,
        with_miss_diagnoses=with_miss_diagnoses,
    )
    miss_diagnoses: tuple[RubberbandMissDiagnosis, ...] | None = None
    if with_miss_diagnoses and (setup is None or diagnose_all_top_bars):
        miss_diagnoses = raw_misses
    return RubberbandScanResult(
        exists=setup is not None,
        session_date=resolved_session,
        setup=setup,
        first_pattern_setup=first_pattern,
        miss_diagnoses=miss_diagnoses,
    )


def rubberband(
    bar_series: BarSeries,
    session_date: date | None = None,
    *,
    atr_extension_min: float = ATR_EXTENSION_MIN,
    rvol_min: float = RVOL_MIN,
    top_sized_bar_count: int = TOP_SIZE_RANK,
    min_snap_open_leg_atr: float = MIN_SNAP_OPEN_LEG_ATR,
    diagnose_all_top_bars: bool = False,
) -> bool:
    """True if a rubber-band setup exists for the session (same as ``rubberband_scan(...).exists``)."""
    return rubberband_scan(
        bar_series,
        session_date=session_date,
        atr_extension_min=atr_extension_min,
        rvol_min=rvol_min,
        top_sized_bar_count=top_sized_bar_count,
        min_snap_open_leg_atr=min_snap_open_leg_atr,
        diagnose_all_top_bars=diagnose_all_top_bars,
    ).exists
