"""Strategy clock gate: ``signal_eligible`` column on the bar table.

``session`` (PM/RTH/AH) is now a catalog indicator computed in :class:`IndicatorPipeline`
via ``strategies.indicators.session:session_series``. This module re-exports
``session_label_series`` for backwards compatibility and adds ``signal_eligible``,
which requires ``SessionConfig`` (strategy-specific allowed sessions and intraday window).
"""

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pandas as pd

from strategies.indicators.session import session_series as session_label_series

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.strategy.strategy_config import SessionConfig
    from backtesting.strategy.strategy_config import SessionLabel

SESSION_COLUMN = 'session'
SIGNAL_ELIGIBLE_COLUMN = 'signal_eligible'


def _clock_to_minute(hh_mm: str) -> int:
    hour_str, minute_str = hh_mm.split(':', 1)
    return int(hour_str) * 60 + int(minute_str)


def signal_eligible_series(
    session: 'pd.Series',
    timestamp_utc: 'pd.Series',
    session_config: 'SessionConfig',
) -> 'pd.Series':
    """True when bar session is allowed and local clock is in ``[intraday_start, intraday_end]``.

    Parameters
    ----------
    session:
        Pre-computed session column (PM/RTH/AH) from the indicator pipeline.
    timestamp_utc:
        UTC bar open timestamps (used for intraday clock window check).
    session_config:
        Strategy session rules.
    """
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
    """Session-derived columns added by the condition pipeline."""
    return SESSION_COLUMN, SIGNAL_ELIGIBLE_COLUMN
