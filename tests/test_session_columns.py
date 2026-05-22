"""Session label and signal_eligible from StrategyConfig."""

from datetime import datetime

import pandas as pd

from backtesting.conditions.condition_pipeline import ConditionPipeline
from backtesting.conditions.session_regime import session_label_series
from backtesting.conditions.session_regime import signal_eligible_series
from backtesting.frames.symbol_bar_frame import SymbolBarFrame
from backtesting.strategy.strategy_config import SessionConfig
from trading.market_timezones import exchange_timezone_name
from trading.market_timezones import zone

UTC = zone('UTC')


def _ts_et(session_date: str, hour: int, minute: int) -> pd.Timestamp:
    et = datetime.fromisoformat(f'{session_date}T{hour:02d}:{minute:02d}')
    utc_dt = et.replace(tzinfo=zone(exchange_timezone_name())).astimezone(UTC)
    return pd.Timestamp(utc_dt)  # pyright: ignore[reportReturnType]


def _ema_cross_session_config() -> SessionConfig:
    return SessionConfig(
        allowed_sessions=('RTH',),
        intraday_start='09:30',
        intraday_end='11:30',
        timezone=exchange_timezone_name(),
    )


def test_session_label_pm_rth_ah() -> None:
    ts = pd.Series(
        [
            _ts_et('2026-05-15', 8, 0),
            _ts_et('2026-05-15', 10, 0),
            _ts_et('2026-05-15', 16, 30),
        ],
    )
    labels = session_label_series(ts, exchange_timezone_name())
    assert labels.iloc[0] == 'PM'
    assert labels.iloc[1] == 'RTH'
    assert labels.iloc[2] == 'AH'


def test_signal_eligible_rth_opening_range() -> None:
    cfg = _ema_cross_session_config()
    ts = pd.Series(
        [
            _ts_et('2026-05-15', 8, 0),
            _ts_et('2026-05-15', 9, 30),
            _ts_et('2026-05-15', 11, 30),
            _ts_et('2026-05-15', 12, 0),
        ],
    )
    eligible = signal_eligible_series(ts, cfg)
    assert not eligible.iloc[0]
    assert eligible.iloc[1]
    assert eligible.iloc[2]
    assert not eligible.iloc[3]


def test_signal_eligible_requires_allowed_session() -> None:
    cfg = SessionConfig(
        allowed_sessions=('RTH',),
        intraday_start='09:30',
        intraday_end='16:00',
        timezone=exchange_timezone_name(),
    )
    ts = pd.Series([_ts_et('2026-05-15', 8, 0)])
    assert not signal_eligible_series(ts, cfg).iloc[0]


def test_session_pipeline_adds_columns() -> None:
    ts = pd.Series([_ts_et('2026-05-15', 10, 0)])
    frame = SymbolBarFrame(
        symbol='TEST',
        interval_minutes=1,
        bars=pd.DataFrame(
            {
                'timestamp': ts,
                'open': [100.0],
                'high': [100.0],
                'low': [100.0],
                'close': [100.0],
                'volume': [1000],
                'vwap': [100.0],
                'symbol': ['TEST'],
            },
        ),
    )
    out = ConditionPipeline(session_config=_ema_cross_session_config()).run(frame)
    assert out.bars.session.iloc[0] == 'RTH'
    assert bool(out.bars.signal_eligible.iloc[0])
