"""Exchange session label and strategy clock gate (regime columns on the bar table).

Uses :class:`~backtesting.strategy.strategy_config.SessionConfig` from strategy YAML
(``allowed_sessions``, ``intraday_start`` / ``intraday_end``, ``timezone``). This is
**not** run-level display timezone for CLI/UI — bars stay UTC; these columns answer
“is this bar in RTH?” and “is this bar inside the strategy’s allowed clock window?”
in the configured IANA zone (typically ``America/New_York`` for US equities).
"""

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pandas as pd

from strategies.utils import RTH_END
from strategies.utils import RTH_START

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.strategy.strategy_config import SessionConfig
    from backtesting.strategy.strategy_config import SessionLabel

SESSION_COLUMN = 'session'
SIGNAL_ELIGIBLE_COLUMN = 'signal_eligible'


def _clock_to_minute(hh_mm: str) -> int:
    hour_str, minute_str = hh_mm.split(':', 1)
    return int(hour_str) * 60 + int(minute_str)


def _rth_bounds_minutes() -> tuple[int, int]:
    return (
        RTH_START.hour * 60 + RTH_START.minute,
        RTH_END.hour * 60 + RTH_END.minute,
    )


def session_label_series(timestamp_utc: 'pd.Series', timezone: str) -> 'pd.Series':
    """Classify each bar as PM, RTH, or AH using US equity session bounds in ``timezone``.

    Uses the same 09:30–16:00 bounds as :mod:`strategies.utils` (desk ET for US equities).
    """
    tz = ZoneInfo(timezone)
    ts_local = pd.to_datetime(timestamp_utc, utc=True).dt.tz_convert(tz)
    minute = ts_local.dt.hour * 60 + ts_local.dt.minute
    rth_lo, rth_hi = _rth_bounds_minutes()
    session = pd.Series('AH', index=timestamp_utc.index, dtype='string')
    session.loc[minute < rth_lo] = 'PM'
    session.loc[(minute >= rth_lo) & (minute < rth_hi)] = 'RTH'
    return session


def signal_eligible_series(
    timestamp_utc: 'pd.Series',
    session_config: 'SessionConfig',
) -> 'pd.Series':
    """True when bar session is allowed and local clock is in ``[intraday_start, intraday_end]``."""
    session = session_label_series(timestamp_utc, session_config.timezone)
    allowed: set[SessionLabel] = set(session_config.allowed_sessions)
    in_allowed_session = session.isin(list(allowed))

    tz = ZoneInfo(session_config.timezone)
    ts_local = pd.to_datetime(timestamp_utc, utc=True).dt.tz_convert(tz)
    minute = ts_local.dt.hour * 60 + ts_local.dt.minute
    start_min = _clock_to_minute(session_config.intraday_start)
    end_min = _clock_to_minute(session_config.intraday_end)
    in_window = (minute >= start_min) & (minute <= end_min)

    return (in_allowed_session & in_window).astype('bool')


def session_column_names() -> tuple[str, str]:
    """Regime columns from :class:`~backtesting.strategy.strategy_config.SessionConfig`."""
    return SESSION_COLUMN, SIGNAL_ELIGIBLE_COLUMN


def apply_session_columns(
    frame: 'SymbolBarFrame',
    session_config: 'SessionConfig',
) -> 'SymbolBarFrame':
    """Add ``session`` and ``signal_eligible`` (multi-bar regime / clock gate)."""
    ts = frame.bars.timestamp
    return frame.with_columns(
        **{
            SESSION_COLUMN: session_label_series(ts, session_config.timezone),
            SIGNAL_ELIGIBLE_COLUMN: signal_eligible_series(ts, session_config),
        },
    )
