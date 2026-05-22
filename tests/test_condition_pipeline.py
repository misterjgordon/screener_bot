"""ConditionPipeline: session regime + optional registry conditions."""

import pandas as pd

from backtesting.conditions.condition_pipeline import ConditionPipeline
from backtesting.frames.symbol_bar_frame import SymbolBarFrame
from backtesting.conditions.session_regime import SESSION_COLUMN
from backtesting.conditions.session_regime import SIGNAL_ELIGIBLE_COLUMN
from backtesting.strategy.strategy_config import SessionConfig

SESSION_CFG = SessionConfig(
    allowed_sessions=('RTH',),
    intraday_start='09:30',
    intraday_end='11:30',
    timezone='America/New_York',
)


def _one_bar_frame() -> SymbolBarFrame:
    ts = pd.Timestamp('2026-05-15 14:30:00', tz='UTC')
    return SymbolBarFrame(
        symbol='TEST',
        interval_minutes=1,
        bars=pd.DataFrame(
            {
                'timestamp': [ts],
                'open': [100.0],
                'high': [101.0],
                'low': [99.0],
                'close': [100.5],
                'volume': [1000.0],
                'vwap': [100.25],
                'symbol': ['TEST'],
            },
        ),
    )


def test_condition_pipeline_session_only() -> None:
    out = ConditionPipeline(session_config=SESSION_CFG).run(_one_bar_frame())

    has_session = SESSION_COLUMN in out.column_names
    has_eligible = SIGNAL_ELIGIBLE_COLUMN in out.column_names
    session_val = out.bars.session.iloc[0]
    eligible_val = bool(out.bars.signal_eligible.iloc[0])

    print(
        '**summary for ConditionPipeline session only:**\n'
        f'has_session = {has_session} | has_signal_eligible = {has_eligible}\n'
        f'session = {session_val} | signal_eligible = {eligible_val}'
    )

    assert has_session
    assert has_eligible
    assert session_val == 'RTH'
    assert eligible_val
