"""Gap indicator: prior close vs reference price (extended-hours or open)."""

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from typing import TYPE_CHECKING

from strategies.indicators.atr import atr
from strategies.utils import RTH_START
from strategies.utils import bar_date
from strategies.utils import bar_session

if TYPE_CHECKING:
    from trading.models import Bar
    from trading.models import BarSeries


def _naive_dt(bar_dt: object) -> datetime | None:
    if isinstance(bar_dt, datetime):
        return bar_dt.replace(tzinfo=None) if bar_dt.tzinfo else bar_dt
    return None


def _prior_session_close_from_1d(bars_1d: list, session_date: date) -> float | None:
    """Last RTH daily close strictly before ``session_date`` (official prior session)."""
    prior_close: float | None = None
    for bar in bars_1d:
        bd = bar_date(bar.date)
        if bd is None or bd >= session_date:
            continue
        prior_close = float(bar.close)
    return prior_close


def _prior_rth_close(bars_2min_sorted: list['Bar'], eval_as_of: datetime) -> float | None:
    last_close: float | None = None
    for bar in bars_2min_sorted:
        dt = _naive_dt(bar.date)
        if dt is None or dt > eval_as_of:
            continue
        if bar_session(dt) == 'RTH':
            last_close = float(bar.close)
    return last_close


def _latest_extended_price(
    bars_2min_sorted: list['Bar'],
    eval_as_of: datetime,
) -> float | None:
    price_out: float | None = None
    for bar in bars_2min_sorted:
        dt = _naive_dt(bar.date)
        if dt is None or dt > eval_as_of:
            continue
        session = bar_session(dt)
        if session in ('PM', 'AH'):
            price_out = float(bar.close)
    return price_out


def _current_session_open(
    bars_2min_sorted: list['Bar'],
    eval_as_of: datetime,
) -> float | None:
    eval_date = eval_as_of.date()
    for bar in bars_2min_sorted:
        dt = _naive_dt(bar.date)
        if dt is None or dt.date() != eval_date:
            continue
        if bar_session(dt) == 'RTH':
            return float(bar.open)
    return None


@dataclass(frozen=True)
class Gap:
    """Gap between prior RTH close and reference price.

    ``gap_points`` is ``reference_price - prior_close``.
    ``gap_percent`` is ``gap_points / prior_close * 100``.
    ``gap_atr`` is ``gap_points / atr_value``.
    """

    prior_close: float
    reference_price: float
    gap_points: float
    gap_percent: float
    atr_value: float | None
    gap_atr: float | None


def gap(
    bar_series: 'BarSeries',
    *,
    atr_period: int = 14,
    eval_as_of: datetime | None = None,
) -> Gap | None:
    """Compute gap from prior close to current extended-hours or session open price.

    Prior close is the last daily (RTH) close before ``eval_as_of``'s session date when
    ``bars_1d`` is provided; otherwise the last RTH 2-minute close at or before
    ``eval_as_of`` (needs multi-day 2-minute history before open).

    Before RTH open (09:30 ET), reference price is the latest extended-hours close
    (AH/PM) up to ``eval_as_of``. At/after RTH open, reference price is current
    session open (first RTH 2-minute bar open).
    """
    bars_2min = bar_series.bars_2min
    if not bars_2min:
        return None

    sorted_2min = sorted(
        bars_2min,
        key=lambda b: _naive_dt(b.date) or datetime.min,
    )
    if eval_as_of is None:
        last_dt = _naive_dt(sorted_2min[-1].date)
        if last_dt is None:
            return None
        eval_as_of = last_dt
    eval_as_of = eval_as_of.replace(tzinfo=None) if eval_as_of.tzinfo else eval_as_of

    session_date = eval_as_of.date()
    prior_close = _prior_session_close_from_1d(bar_series.bars_1d, session_date)
    if prior_close is None or prior_close <= 0:
        prior_close = _prior_rth_close(sorted_2min, eval_as_of)
    if prior_close is None or prior_close <= 0:
        return None

    if eval_as_of.time() < RTH_START:
        reference_price = _latest_extended_price(sorted_2min, eval_as_of)
    else:
        reference_price = _current_session_open(sorted_2min, eval_as_of)
    if reference_price is None:
        return None

    gap_points = reference_price - prior_close
    gap_percent = (gap_points / prior_close) * 100.0

    atr_value = atr(bar_series.bars_1d, period=atr_period)
    gap_atr = (gap_points / atr_value) if atr_value is not None and atr_value > 0 else None

    return Gap(
        prior_close=round(prior_close, 4),
        reference_price=round(reference_price, 4),
        gap_points=round(gap_points, 4),
        gap_percent=round(gap_percent, 4),
        atr_value=atr_value,
        gap_atr=round(gap_atr, 4) if gap_atr is not None else None,
    )


def gap_percent(
    bar_series: 'BarSeries',
    *,
    eval_as_of: datetime | None = None,
) -> float | None:
    """Percent gap helper."""
    gap_value = gap(bar_series, eval_as_of=eval_as_of)
    if gap_value is None:
        return None
    return gap_value.gap_percent


def gap_atr(
    bar_series: 'BarSeries',
    *,
    atr_period: int = 14,
    eval_as_of: datetime | None = None,
) -> float | None:
    """Gap in ATR units helper."""
    gap_value = gap(bar_series, atr_period=atr_period, eval_as_of=eval_as_of)
    if gap_value is None:
        return None
    return gap_value.gap_atr
