"""Percent of average daily volume (intraday windows vs N-day mean of prior full days).

Mirrors TradingView logic in ``scripts/pine/percent_of_avg_volume_pine.py`` on **2-minute** bars:
five 2m bars approximate a 10-minute Pine window. Session bounds match Pine (ET, naive
datetimes): after-hours combo 16:00–20:00, premarket 04:00–09:30, RTH 09:30–16:00.

**Volume sections (desk workflow, PT):** section 1 is prior regular session after-hours
(D0 AH, ET 16:00–20:00) plus the current session premarket (D1 pre, ET 04:00–09:30)—the
same instant as **6:30 AM PT** is NYSE/Nasdaq cash open (``RTH_START``). Active volume uses
that combo while ``eval_as_of`` is strictly before open on ``session_date``. Section 2 is
D1 RTH through close—the same wall clock as **6:30 AM–1:00 PM PT** (``RTH_START``–``RTH_END`` ET).

Denominator: mean of the last ``average_length`` daily volumes **excluding** the last bar
in ``bars_1d`` (same prior-window convention as :func:`strategies.indicators.rvol.rvol`).

``bars_2min`` should include extended hours (``use_rth=False`` load). Premarket combo
(D0 AH + D1 pre) needs prior-session AH bars in the series when possible; a short
``1 D`` request may omit them—combo then falls back to pre-only cumulation.
"""

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from typing import TYPE_CHECKING

from strategies.utils import RTH_END
from strategies.utils import RTH_START
from strategies.utils import bar_date
from strategies.utils import bar_session

if TYPE_CHECKING:
    from trading.models import BarSeries

# Pine defaults
DEFAULT_THRESHOLD_PCT = 25.0
DEFAULT_AVERAGE_LENGTH = 30
DEFAULT_WINDOW_MINUTES = 10

# Minute-of-day (ET) bounds for premkt combo (Pine ``premarket_combo_1m``)
_PRE_START_MIN = 4 * 60
_PRE_END_MIN = 9 * 60 + 30
_POST_START_MIN = 16 * 60
_POST_END_MIN = 20 * 60


def _rth_start_minutes() -> int:
    return RTH_START.hour * 60 + RTH_START.minute


def _first_rth_window_end_minutes(window_minutes: int) -> int:
    return _rth_start_minutes() + window_minutes


def _minutes_to_time(total_minutes: int) -> time:
    return time(total_minutes // 60, total_minutes % 60)


def _naive_dt(bar_dt: object) -> datetime | None:
    if isinstance(bar_dt, datetime):
        return bar_dt.replace(tzinfo=None) if bar_dt.tzinfo else bar_dt
    return None


def _minute_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _in_post_combo(dt: datetime) -> bool:
    m = _minute_of_day(dt)
    return _POST_START_MIN <= m < _POST_END_MIN


def _in_pre_combo(dt: datetime) -> bool:
    m = _minute_of_day(dt)
    return _PRE_START_MIN <= m < _PRE_END_MIN


def _average_daily_volume(bars_1d: list, average_length: int) -> float | None:
    if len(bars_1d) < average_length + 1:
        return None
    prior_volumes = [float(b.volume) for b in bars_1d[-average_length - 1: -1]]
    avg_vol = sum(prior_volumes) / average_length
    if avg_vol <= 0:
        return None
    return avg_vol


def _premarket_combo_final(bars_sorted: list) -> float | None:
    post_acc = 0.0
    post_total_prev: float | None = None
    pre_acc = 0.0
    prev_dt: datetime | None = None

    for bar in bars_sorted:
        dt = _naive_dt(bar.date)
        if dt is None:
            prev_dt = None
            continue
        is_post = _in_post_combo(dt)
        is_pre = _in_pre_combo(dt)
        was_post = _in_post_combo(prev_dt) if prev_dt is not None else False
        was_pre = _in_pre_combo(prev_dt) if prev_dt is not None else False

        if is_post:
            post_acc += float(bar.volume)

        if (not is_post) and was_post:
            post_total_prev = post_acc
            post_acc = 0.0

        if is_pre and not was_pre:
            pre_acc = 0.0

        if is_pre:
            pre_acc += float(bar.volume)

        prev_dt = dt

    if not bars_sorted:
        return None
    last_dt = _naive_dt(bars_sorted[-1].date)
    if last_dt is not None and _in_pre_combo(last_dt):
        return (post_total_prev or 0.0) + pre_acc
    return None


def _rth_bars_session_chronological(
    bars_sorted: list,
    session_date: object,
    eval_as_of: datetime,
) -> list:
    out: list = []
    for bar in bars_sorted:
        dt = _naive_dt(bar.date)
        if dt is None or dt > eval_as_of:
            continue
        bd = bar_date(dt)
        if bd != session_date:
            continue
        if bar_session(dt) == 'RTH':
            out.append(bar)
    return out


@dataclass(frozen=True)
class PercentOfAvgVolume:
    """Intraday cumulative-window volumes vs prior-day average and threshold."""

    average_volume: float
    threshold_pct: float
    window_minutes: int
    premarket_combo: float | None
    first_window_cumulative: float | None
    first_window_frozen: float | None
    trailing_volume: float | None
    threshold_locked: bool
    locked_vol: float | None
    active_volume: float | None
    percent_of_average: float | None
    above_threshold: bool


def percent_of_avg_volume(
    bar_series: 'BarSeries',
    *,
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
    average_length: int = DEFAULT_AVERAGE_LENGTH,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    eval_as_of: datetime | None = None,
) -> PercentOfAvgVolume | None:
    """Compute percent-of-average-volume state for the bar series.

    Provide ``bars_1d`` whose last row is the most recent **complete** session you want
    anchored for the mean (see ``tests.test_rvol._slice_bars_1d``). ``bars_2min`` should
    include all intraday extended bars through the evaluation instant.

    ``eval_as_of``: defaults to the timestamp of the last 2m bar (naive ET).
    """
    bars_1d = bar_series.bars_1d
    bars_2min = bar_series.bars_2min
    if not bars_2min:
        return None

    avg_vol = _average_daily_volume(bars_1d, average_length)
    if avg_vol is None:
        return None

    sorted_2m = sorted(
        bars_2min,
        key=lambda b: _naive_dt(b.date) or datetime.min,
    )
    if eval_as_of is None:
        last = _naive_dt(sorted_2m[-1].date)
        if last is None:
            return None
        eval_as_of = last

    eval_as_of = eval_as_of.replace(tzinfo=None) if eval_as_of.tzinfo else eval_as_of

    upto = [b for b in sorted_2m if (_naive_dt(b.date) or datetime.max) <= eval_as_of]
    if not upto:
        return None

    session_date: date = eval_as_of.date()
    pre_combo = _premarket_combo_final(upto)

    rth_bars = _rth_bars_session_chronological(sorted_2m, session_date, eval_as_of)
    first_end_m = _first_rth_window_end_minutes(window_minutes)
    rth_start_m = _rth_start_minutes()
    n_trail = max(1, window_minutes // 2)

    all_rth_vols: list[float] = []
    cum_first = 0.0
    first_frozen: float | None = None
    threshold_locked = False
    locked_vol: float | None = None
    trailing_at_eval: float | None = None

    for bar in rth_bars:
        dt = _naive_dt(bar.date)
        if dt is None:
            continue
        m = _minute_of_day(dt)
        v = float(bar.volume)
        if m < rth_start_m:
            continue

        all_rth_vols.append(v)
        i = len(all_rth_vols) - 1

        if m < first_end_m:
            cum_first += v
            pct_run = (cum_first / avg_vol) * 100.0
            if not threshold_locked and pct_run >= threshold_pct:
                threshold_locked = True
                locked_vol = cum_first
        else:
            if first_frozen is None:
                first_frozen = cum_first
            trailing_at_eval = sum(all_rth_vols[max(0, i + 1 - n_trail): i + 1])
            pct_tr = (trailing_at_eval / avg_vol) * 100.0
            if not threshold_locked and pct_tr >= threshold_pct:
                threshold_locked = True
                locked_vol = trailing_at_eval

    eval_t = eval_as_of.time()
    if (
        not threshold_locked
        and first_frozen is not None
        and eval_t >= RTH_END
    ):
        threshold_locked = True
        locked_vol = first_frozen

    first_end_time = _minutes_to_time(first_end_m)
    eval_m = _minute_of_day(eval_as_of)

    first_window_cumulative_out: float | None = None
    if rth_bars and rth_start_m <= eval_m < first_end_m:
        first_window_cumulative_out = cum_first

    trailing_out: float | None = None
    if rth_bars and eval_m >= first_end_m and trailing_at_eval is not None:
        trailing_out = trailing_at_eval

    # Section 1: D0 AH + D1 pre until open (9:30 ET == 6:30 AM PT for US cash equities).
    active: float | None = None
    rth_open_session_et = datetime.combine(session_date, RTH_START)
    use_premarket_branch = eval_as_of < rth_open_session_et
    if use_premarket_branch:
        active = pre_combo
    elif rth_bars:
        if threshold_locked and locked_vol is not None:
            active = locked_vol
        elif eval_m < first_end_m:
            active = cum_first
        else:
            active = trailing_at_eval

    pct: float | None = None
    if active is not None and avg_vol > 0:
        pct = (active / avg_vol) * 100.0

    above = pct is not None and pct >= threshold_pct

    return PercentOfAvgVolume(
        average_volume=avg_vol,
        threshold_pct=threshold_pct,
        window_minutes=window_minutes,
        premarket_combo=pre_combo,
        first_window_cumulative=first_window_cumulative_out,
        first_window_frozen=first_frozen,
        trailing_volume=trailing_out,
        threshold_locked=threshold_locked,
        locked_vol=locked_vol,
        active_volume=active,
        percent_of_average=pct,
        above_threshold=above,
    )
