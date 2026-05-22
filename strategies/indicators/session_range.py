"""Desk session OHLC on 2-minute bars with ADR-normalized session range.

Sessions are defined in **America/Los_Angeles** (PT). Bar timestamps follow the
project convention: **naive US/Eastern** (``strategies.utils`` / IB).

Windows are half-open ``[start, end)`` on bar **start** time (2m bar ``date``).

**ADR (average daily range)** is the mean of daily high-low over N sessions; see
:mod:`strategies.indicators.adr` and :func:`~strategies.indicators.adr.calculate_adr`.

**Session range fields:** ``change`` is **high minus low** over the window (the span),
**rounded to 2 decimals**. When ADR is finite and ``> 0``, ``adr_change_percent`` is
``(high - low) / ADR`` with a **sign** from session direction: positive when ``close > open``,
negative when ``close < open``. When ``close == open`` (no net direction), the ratio stays
**positive** (unsigned range). Same unitless ratio as before (not multiplied by ``100``),
**rounded to 2 decimals**.
Example: ADR ``1.0``, range ``0.80``, net up → ``adr_change_percent`` ``0.8``; net down → ``-0.8``.
Pass ``adr`` from :func:`strategies.indicators.adr.calculate_adr`, or pass ``bars_1d``
and leave ``adr`` unset so this module calls :func:`~strategies.indicators.adr.calculate_adr` on that series.

``prior_day_ah_session`` uses the **prior trading session date** (weekend-aware)
and 13:00–17:00 PT that day (typically 16:00–20:00 ET extended hours).

When fetching 2m bars for ``session_date`` (anchor desk day), request **two
calendar days** of history (IB ``durationStr`` ``'2 D'`` ending on that day) so
prior-day after-hours is usually included. Use :data:`BARS_2MIN_DURATION_FOR_DESK_SESSION_RANGES`
with :func:`trading.bar_loader.load_bars`'s ``duration_str_2min`` argument;
``load_bars``'s default ``1 D`` 2m slice may omit prior AH.
"""

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta

from strategies.indicators.adr import calculate_adr
from strategies.utils import last_trading_day
from trading.market_timezones import display_zone
from trading.market_timezones import exchange_zone
from trading.models import BarSeries

# IB reqHistoricalData duration for 2m bars when aggregating desk windows for ``session_date``.
BARS_2MIN_DURATION_FOR_DESK_SESSION_RANGES = '2 D'

# PT session bounds (desk clock, America/Los_Angeles)
_PRIOR_DAY_AH_START = time(13, 0)
_PRIOR_DAY_AH_END = time(17, 0)
_PM_START = time(1, 0)
_PM_END = time(6, 30)
_OPENING_RANGE_START = time(6, 30)
_OPENING_RANGE_END = time(6, 45)
_MORNING_START = time(6, 45)
_MORNING_END = time(8, 30)
_AFTERNOON_START = time(8, 30)
_AFTERNOON_END = time(11, 0)
_CLOSING_START = time(11, 0)
_CLOSING_END = time(13, 0)


def _to_script_clock_datetime(bar_dt: object) -> datetime | None:
    """Normalize user/bar datetime into script clock (naive in ``_SCRIPT_TZ``)."""
    if isinstance(bar_dt, datetime):
        if bar_dt.tzinfo is None:
            return bar_dt
        return bar_dt.astimezone(exchange_zone()).replace(tzinfo=None)
    return None


def prior_trading_session_date(session_date: date) -> date:
    """Calendar date of the last equity session strictly before ``session_date``."""
    return last_trading_day(session_date - timedelta(days=1))


def _desk_window_to_script_bounds(
    anchor_date: date,
    start_local: time,
    end_local: time,
) -> tuple[datetime, datetime]:
    """Map [start_local, end_local) on anchor_date in desk clock to script clock."""
    start_desk = datetime.combine(anchor_date, start_local, tzinfo=display_zone())
    end_desk = datetime.combine(anchor_date, end_local, tzinfo=display_zone())
    start_script = start_desk.astimezone(exchange_zone()).replace(tzinfo=None)
    end_script = end_desk.astimezone(exchange_zone()).replace(tzinfo=None)
    return (start_script, end_script)


def _bars_in_window(
    bars_sorted: list,
    start_dt: datetime,
    end_dt: datetime,
    eval_as_of: datetime | None,
) -> list:
    out: list = []
    for bar in bars_sorted:
        dt = _to_script_clock_datetime(bar.date)
        if dt is None:
            continue
        if eval_as_of is not None and dt > eval_as_of:
            continue
        if start_dt <= dt < end_dt:
            out.append(bar)
    return out


def _aggregate_session(
    window_bars: list,
    adr: float | None,
) -> 'SessionOhlcAdr':
    if not window_bars:
        return SessionOhlcAdr(
            open=None,
            high=None,
            low=None,
            close=None,
            change=None,
            adr_change_percent=None,
        )
    o = float(window_bars[0].open)
    hi = max(float(b.high) for b in window_bars)
    lo = min(float(b.low) for b in window_bars)
    c = float(window_bars[-1].close)
    change = hi - lo
    net_move = c - o
    ratio: float | None = None
    if adr is not None and adr > 0:
        signed_range = change if net_move >= 0 else -change
        ratio = round(signed_range / adr, 2)
    return SessionOhlcAdr(
        open=o,
        high=hi,
        low=lo,
        close=c,
        change=round(change, 2),
        adr_change_percent=ratio,
    )


@dataclass(frozen=True)
class SessionOhlcAdr:
    """OHLC plus span ``change`` (high minus low) and ADR-normalized signed range."""

    open: float | None
    high: float | None
    low: float | None
    close: float | None
    change: float | None
    adr_change_percent: float | None


@dataclass(frozen=True)
class DeskSessionRanges:
    """Six desk sessions (PT definitions, naive-ET bar timestamps).

    Each :class:`SessionOhlcAdr` stores ``change`` (high minus low, 2 decimals) and
    ``adr_change_percent`` (signed ``(high - low) / ADR`` from net direction; 2 decimals).
    """

    prior_day_ah_session: SessionOhlcAdr
    pm_session: SessionOhlcAdr
    opening_range_session: SessionOhlcAdr
    morning_session: SessionOhlcAdr
    afternoon_session: SessionOhlcAdr
    closing_session: SessionOhlcAdr


def compute_desk_session_ranges(
    bars_2min: list,
    *,
    session_date: date,
    adr: float | None = None,
    bars_1d: list | None = None,
    eval_as_of: datetime | None = None,
) -> DeskSessionRanges | None:
    """Aggregate 2m bars into desk session OHLC, span (high-low), and signed range vs ADR.

    Parameters
    ----------
    bars_2min
        Chronological or unsorted 2-minute bars (sorted internally).
    session_date
        Trading session calendar date in **ET** (same convention as bar dates).
    adr
        Average daily range in dollars from :func:`~strategies.indicators.adr.calculate_adr`.
        If omitted and ``bars_1d`` is set, ADR is computed here via that same function
        (with ``BarSeries(bars_1d=bars_1d, bars_2min=[])``; ``ib``/``symbol`` are unused).
    bars_1d
        Daily bars used only when ``adr`` is ``None`` to call ``calculate_adr``.
    eval_as_of
        If set, only bars with start time ``<= eval_as_of`` are used. Naive values
        are treated as script clock; aware values are converted to script clock.
    """
    if not bars_2min:
        return None

    adr_effective = adr
    if adr_effective is None and bars_1d is not None:
        adr_effective = calculate_adr(
            None,
            '',
            bundle=BarSeries(bars_1d=bars_1d, bars_2min=[]),
        )

    eval_as_of_script = _to_script_clock_datetime(eval_as_of)
    sorted_bars = sorted(
        bars_2min,
        key=lambda b: _to_script_clock_datetime(b.date) or datetime.min,
    )

    prior_day = prior_trading_session_date(session_date)
    ah_start_dt, ah_end_dt = _desk_window_to_script_bounds(
        prior_day,
        _PRIOR_DAY_AH_START,
        _PRIOR_DAY_AH_END,
    )
    pm_start_dt, pm_end_dt = _desk_window_to_script_bounds(
        session_date,
        _PM_START,
        _PM_END,
    )
    or_start_dt, or_end_dt = _desk_window_to_script_bounds(
        session_date,
        _OPENING_RANGE_START,
        _OPENING_RANGE_END,
    )
    morn_start_dt, morn_end_dt = _desk_window_to_script_bounds(
        session_date,
        _MORNING_START,
        _MORNING_END,
    )
    aft_start_dt, aft_end_dt = _desk_window_to_script_bounds(
        session_date,
        _AFTERNOON_START,
        _AFTERNOON_END,
    )
    close_start_dt, close_end_dt = _desk_window_to_script_bounds(
        session_date,
        _CLOSING_START,
        _CLOSING_END,
    )

    prior_ah = _bars_in_window(sorted_bars, ah_start_dt, ah_end_dt, eval_as_of_script)
    pm = _bars_in_window(sorted_bars, pm_start_dt, pm_end_dt, eval_as_of_script)
    opening_range = _bars_in_window(sorted_bars, or_start_dt, or_end_dt, eval_as_of_script)
    morning = _bars_in_window(sorted_bars, morn_start_dt, morn_end_dt, eval_as_of_script)
    afternoon = _bars_in_window(sorted_bars, aft_start_dt, aft_end_dt, eval_as_of_script)
    closing = _bars_in_window(sorted_bars, close_start_dt, close_end_dt, eval_as_of_script)

    return DeskSessionRanges(
        prior_day_ah_session=_aggregate_session(prior_ah, adr_effective),
        pm_session=_aggregate_session(pm, adr_effective),
        opening_range_session=_aggregate_session(opening_range, adr_effective),
        morning_session=_aggregate_session(morning, adr_effective),
        afternoon_session=_aggregate_session(afternoon, adr_effective),
        closing_session=_aggregate_session(closing, adr_effective),
    )
