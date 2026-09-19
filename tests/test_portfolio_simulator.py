"""Portfolio simulation: entry_event → fills, stops, targets, end_of_session (step 9)."""

import pandas as pd
import pytest

from backtesting.frames.symbol_bar_frame import SymbolBarFrame
from backtesting.frames.universe_bar_frames import UniverseBarFrames
from backtesting.portfolio.exit_levels import exit_levels
from backtesting.portfolio.exit_levels import has_end_of_session_other
from backtesting.portfolio.portfolio_simulator import PortfolioSimulator
from backtesting.portfolio.symbol_simulator import PortfolioSimError
from backtesting.portfolio.symbol_simulator import simulate_symbol_trades
from backtesting.portfolio.symbol_simulator import symbol_from_frame
from backtesting.signals.entry_columns import ENTRY_EVENT_COLUMN
from backtesting.signals.signal_columns import SIGNAL_EXIT_HIT_COLUMN
from backtesting.strategy.strategy_config import OtherExitClosePastEma
from backtesting.strategy.strategy_config import OtherExitEndOfSession
from backtesting.strategy.strategy_config import SizingFixedDollars
from backtesting.strategy.strategy_config import SizingFullAllocation
from backtesting.strategy.strategy_config import StopLossPctFromEntry
from backtesting.strategy.strategy_config import StrategyConfig
from backtesting.strategy.strategy_config import StrategySide
from backtesting.strategy.strategy_config import TakeProfitPctFromEntry
from strategies.exit.stop_loss.pct_from_entry import stop_loss_price_long
from strategies.exit.stop_loss.pct_from_entry import stop_loss_price_short
from strategies.exit.take_profit.pct_from_entry import take_profit_price_long
from strategies.exit.take_profit.pct_from_entry import take_profit_price_short
from tests.strategy_signal_test_support import frame_with_session
from tests.strategy_signal_test_support import load_strategy

STRATEGY_ID = 'ema_cross'


def _fixed_dollars_amount(strategy: StrategyConfig) -> float:
    """``sizing.amount`` for a ``fixed_dollars`` fixture strategy (all fixtures in this file)."""
    assert isinstance(strategy.sizing, SizingFixedDollars)
    return strategy.sizing.amount


def _fixture_entry_close(strategy: StrategyConfig) -> float:
    """Synthetic entry-bar close for OHLC fixtures (``sizing.amount / 50`` → 100 for ema_cross)."""
    return round(_fixed_dollars_amount(strategy) / 50.0, 2)


def _entry_close_from_bars(ohlc_by_bar: list[dict[str, float]], entry_bar: int) -> float:
    """Entry fill = close on the entry bar (same rule as production sim)."""
    return float(ohlc_by_bar[entry_bar]['close'])


def _ohlc_bar(
    *,
    open_px: float,
    high_px: float,
    low_px: float,
    close_px: float,
) -> dict[str, float]:
    return {'open': open_px, 'high': high_px, 'low': low_px, 'close': close_px}


def _flat_ohlc(close_px: float) -> dict[str, float]:
    return _ohlc_bar(open_px=close_px, high_px=close_px, low_px=close_px, close_px=close_px)


def _frame_for_sim(
    strategy: StrategyConfig,
    bar_count: int,
    ohlc_by_bar: list[dict[str, float]],
    entry_bar: int,
) -> SymbolBarFrame:
    """Bars with session columns and a single ``entry_event`` on ``entry_bar``."""
    if len(ohlc_by_bar) != bar_count:
        msg = f'ohlc_by_bar length {len(ohlc_by_bar)} != bar_count {bar_count}'
        raise ValueError(msg)

    series_kw: dict[str, pd.Series] = {}
    for key in ('open', 'high', 'low', 'close'):
        series_kw[key] = pd.Series([row[key] for row in ohlc_by_bar])

    frame = frame_with_session(strategy, **series_kw)
    entry_event = pd.Series(False, index=frame.bars.index)
    entry_event.iloc[entry_bar] = True
    return frame.with_columns(**{ENTRY_EVENT_COLUMN: entry_event})


def _strategy_without_other_exits(strategy: StrategyConfig) -> StrategyConfig:
    return strategy.model_copy(update={'other_exits': ()})


def _short_strategy(strategy: StrategyConfig) -> StrategyConfig:
    return strategy.model_copy(update={'side': 'short'})


EMA_COLUMN = 'ema21'


def _strategy_with_close_past_ema(strategy: StrategyConfig, side: StrategySide) -> StrategyConfig:
    rule = OtherExitClosePastEma(
        id='close_past_ema21',
        type='close_past_ema',
        side=side,
        ema_column=EMA_COLUMN,
    )
    return strategy.model_copy(update={'side': side, 'other_exits': (rule,)})


def test_exit_levels_from_entry_fill() -> None:
    strategy = load_strategy(STRATEGY_ID)
    entry_close = _fixture_entry_close(strategy)
    levels = exit_levels(entry_close, strategy.stop_loss, strategy.take_profit, strategy.side)

    stop = levels.stop_price
    target = levels.take_profit_price
    eos_rule = has_end_of_session_other(strategy.other_exits)

    print(
        '**summary for exit_levels:**\n'
        f'stop = {stop} | target = {target} | has_end_of_session = {eos_rule}'
    )

    assert isinstance(strategy.stop_loss, StopLossPctFromEntry)
    assert isinstance(strategy.take_profit, TakeProfitPctFromEntry)
    assert stop == stop_loss_price_long(entry_close, strategy.stop_loss.pct)
    assert target == take_profit_price_long(entry_close, strategy.take_profit.pct)
    assert eos_rule is True


def test_stop_loss_exit_on_next_bar() -> None:
    strategy = _strategy_without_other_exits(load_strategy(STRATEGY_ID))
    entry_close = _fixture_entry_close(strategy)
    stop_px = stop_loss_price_long(entry_close, strategy.stop_loss.pct)
    ohlc_by_bar = [
        _flat_ohlc(entry_close),
        _flat_ohlc(entry_close),
        _flat_ohlc(entry_close),
        _ohlc_bar(
            open_px=entry_close,
            high_px=entry_close,
            low_px=stop_px - 0.5,
            close_px=stop_px,
        ),
    ]
    entry_bar = 2
    frame = _frame_for_sim(strategy, bar_count=4, ohlc_by_bar=ohlc_by_bar, entry_bar=entry_bar)
    trades = simulate_symbol_trades(frame, strategy)
    entry_fill = _entry_close_from_bars(ohlc_by_bar, entry_bar)
    shares = _fixed_dollars_amount(strategy) / entry_fill

    trade_count = len(trades)
    exit_reason = trades[0].exit_reason if trades else None
    exit_price = trades[0].exit_price if trades else None
    pnl = trades[0].pnl if trades else None

    print(
        '**summary for stop_loss exit:**\n'
        f'trade_count = {trade_count} | exit_reason = {exit_reason}\n'
        f'exit_price = {exit_price} | pnl = {pnl}'
    )

    assert trade_count == 1
    assert exit_reason == 'stop_loss'
    assert exit_price == pytest.approx(stop_px)
    assert trades[0].entry_price == pytest.approx(entry_fill)
    assert trades[0].shares == pytest.approx(shares)
    assert pnl == pytest.approx((stop_px - entry_fill) * shares)


def test_take_profit_exit() -> None:
    strategy = _strategy_without_other_exits(load_strategy(STRATEGY_ID))
    entry_close = _fixture_entry_close(strategy)
    target_px = take_profit_price_long(entry_close, strategy.take_profit.pct)
    ohlc_by_bar = [
        _flat_ohlc(entry_close),
        _flat_ohlc(entry_close),
        _ohlc_bar(
            open_px=entry_close,
            high_px=target_px + 0.5,
            low_px=entry_close,
            close_px=target_px + 0.1,
        ),
    ]
    entry_bar = 1
    frame = _frame_for_sim(strategy, bar_count=3, ohlc_by_bar=ohlc_by_bar, entry_bar=entry_bar)
    trades = simulate_symbol_trades(frame, strategy)
    entry_fill = _entry_close_from_bars(ohlc_by_bar, entry_bar)

    exit_reason = trades[0].exit_reason
    exit_price = trades[0].exit_price

    print(
        '**summary for take_profit exit:**\n'
        f'exit_reason = {exit_reason} | exit_price = {exit_price}'
    )

    assert exit_reason == 'take_profit'
    assert exit_price == pytest.approx(target_px)
    assert trades[0].entry_price == pytest.approx(entry_fill)


def test_stop_before_take_profit_on_same_bar() -> None:
    strategy = _strategy_without_other_exits(load_strategy(STRATEGY_ID))
    entry_close = _fixture_entry_close(strategy)
    stop_px = stop_loss_price_long(entry_close, strategy.stop_loss.pct)
    target_px = take_profit_price_long(entry_close, strategy.take_profit.pct)
    frame = _frame_for_sim(
        strategy,
        bar_count=3,
        ohlc_by_bar=[
            _flat_ohlc(entry_close),
            _flat_ohlc(entry_close),
            _ohlc_bar(
                open_px=entry_close,
                high_px=target_px + 0.1,
                low_px=stop_px - 0.1,
                close_px=entry_close,
            ),
        ],
        entry_bar=1,
    )
    trades = simulate_symbol_trades(frame, strategy)

    print(
        '**summary for stop before target:**\n'
        f'exit_reason = {trades[0].exit_reason}'
    )

    assert trades[0].exit_reason == 'stop_loss'


def test_exit_levels_short() -> None:
    strategy = _short_strategy(load_strategy(STRATEGY_ID))
    entry_close = _fixture_entry_close(strategy)
    levels = exit_levels(entry_close, strategy.stop_loss, strategy.take_profit, strategy.side)

    print(
        '**summary for exit_levels short:**\n'
        f'stop = {levels.stop_price} | target = {levels.take_profit_price}'
    )

    assert levels.stop_price == stop_loss_price_short(entry_close, strategy.stop_loss.pct)
    assert levels.take_profit_price == take_profit_price_short(entry_close, strategy.take_profit.pct)
    assert levels.stop_price > entry_close
    assert levels.take_profit_price < entry_close


def test_stop_loss_exit_on_next_bar_short() -> None:
    strategy = _short_strategy(_strategy_without_other_exits(load_strategy(STRATEGY_ID)))
    entry_close = _fixture_entry_close(strategy)
    stop_px = stop_loss_price_short(entry_close, strategy.stop_loss.pct)
    ohlc_by_bar = [
        _flat_ohlc(entry_close),
        _flat_ohlc(entry_close),
        _flat_ohlc(entry_close),
        _ohlc_bar(
            open_px=entry_close,
            high_px=stop_px,
            low_px=entry_close,
            close_px=stop_px - 0.5,
        ),
    ]
    entry_bar = 2
    frame = _frame_for_sim(strategy, bar_count=4, ohlc_by_bar=ohlc_by_bar, entry_bar=entry_bar)
    trades = simulate_symbol_trades(frame, strategy)
    entry_fill = _entry_close_from_bars(ohlc_by_bar, entry_bar)
    shares = _fixed_dollars_amount(strategy) / entry_fill

    trade_count = len(trades)
    exit_reason = trades[0].exit_reason if trades else None
    exit_price = trades[0].exit_price if trades else None
    pnl = trades[0].pnl if trades else None

    print(
        '**summary for short stop_loss exit:**\n'
        f'trade_count = {trade_count} | exit_reason = {exit_reason}\n'
        f'exit_price = {exit_price} | pnl = {pnl}'
    )

    assert trade_count == 1
    assert exit_reason == 'stop_loss'
    assert exit_price == pytest.approx(stop_px)
    assert trades[0].entry_price == pytest.approx(entry_fill)
    assert trades[0].shares == pytest.approx(shares)
    assert pnl == pytest.approx((entry_fill - stop_px) * shares)


def test_take_profit_exit_short() -> None:
    strategy = _short_strategy(_strategy_without_other_exits(load_strategy(STRATEGY_ID)))
    entry_close = _fixture_entry_close(strategy)
    target_px = take_profit_price_short(entry_close, strategy.take_profit.pct)
    ohlc_by_bar = [
        _flat_ohlc(entry_close),
        _flat_ohlc(entry_close),
        _ohlc_bar(
            open_px=entry_close,
            high_px=entry_close,
            low_px=target_px - 0.5,
            close_px=target_px - 0.1,
        ),
    ]
    entry_bar = 1
    frame = _frame_for_sim(strategy, bar_count=3, ohlc_by_bar=ohlc_by_bar, entry_bar=entry_bar)
    trades = simulate_symbol_trades(frame, strategy)
    entry_fill = _entry_close_from_bars(ohlc_by_bar, entry_bar)

    exit_reason = trades[0].exit_reason
    exit_price = trades[0].exit_price
    pnl = trades[0].pnl

    print(
        '**summary for short take_profit exit:**\n'
        f'exit_reason = {exit_reason} | exit_price = {exit_price} | pnl = {pnl}'
    )

    assert exit_reason == 'take_profit'
    assert exit_price == pytest.approx(target_px)
    assert trades[0].entry_price == pytest.approx(entry_fill)
    assert pnl > 0


def test_stop_before_take_profit_on_same_bar_short() -> None:
    strategy = _short_strategy(_strategy_without_other_exits(load_strategy(STRATEGY_ID)))
    entry_close = _fixture_entry_close(strategy)
    stop_px = stop_loss_price_short(entry_close, strategy.stop_loss.pct)
    target_px = take_profit_price_short(entry_close, strategy.take_profit.pct)
    frame = _frame_for_sim(
        strategy,
        bar_count=3,
        ohlc_by_bar=[
            _flat_ohlc(entry_close),
            _flat_ohlc(entry_close),
            _ohlc_bar(
                open_px=entry_close,
                high_px=stop_px + 0.1,
                low_px=target_px - 0.1,
                close_px=entry_close,
            ),
        ],
        entry_bar=1,
    )
    trades = simulate_symbol_trades(frame, strategy)

    print(
        '**summary for short stop before target:**\n'
        f'exit_reason = {trades[0].exit_reason}'
    )

    assert trades[0].exit_reason == 'stop_loss'


def test_end_of_session_exit_at_last_rth_bar() -> None:
    strategy = load_strategy(STRATEGY_ID)
    assert any(isinstance(rule, OtherExitEndOfSession) for rule in strategy.other_exits)
    entry_close = _fixture_entry_close(strategy)
    last_close = entry_close + 1.5
    frame = _frame_for_sim(
        strategy,
        bar_count=4,
        ohlc_by_bar=[
            _flat_ohlc(entry_close),
            _flat_ohlc(entry_close),
            _flat_ohlc(entry_close),
            _flat_ohlc(last_close),
        ],
        entry_bar=1,
    )
    trades = simulate_symbol_trades(frame, strategy)

    exit_reason = trades[0].exit_reason
    exit_price = trades[0].exit_price

    print(
        '**summary for end_of_session:**\n'
        f'exit_reason = {exit_reason} | exit_price = {exit_price}'
    )

    assert exit_reason == 'end_of_session'
    assert exit_price == pytest.approx(last_close)
    assert trades[0].entry_price == pytest.approx(entry_close)


def test_close_past_ema_exit_long() -> None:
    strategy = _strategy_with_close_past_ema(load_strategy(STRATEGY_ID), 'long')
    entry_close = _fixture_entry_close(strategy)
    frame = _frame_for_sim(
        strategy,
        bar_count=3,
        ohlc_by_bar=[
            _flat_ohlc(entry_close),
            _flat_ohlc(entry_close),
            _flat_ohlc(entry_close),
        ],
        entry_bar=1,
    )
    frame = frame.with_columns(
        **{EMA_COLUMN: pd.Series([entry_close, entry_close, entry_close + 5.0])},
    )
    trades = simulate_symbol_trades(frame, strategy)

    exit_reason = trades[0].exit_reason
    exit_price = trades[0].exit_price

    print(
        '**summary for close_past_ema long:**\n'
        f'exit_reason = {exit_reason} | exit_price = {exit_price}'
    )

    assert exit_reason == 'close_past_ema'
    assert exit_price == pytest.approx(entry_close)


def test_close_past_ema_exit_short() -> None:
    strategy = _strategy_with_close_past_ema(load_strategy(STRATEGY_ID), 'short')
    entry_close = _fixture_entry_close(strategy)
    frame = _frame_for_sim(
        strategy,
        bar_count=3,
        ohlc_by_bar=[
            _flat_ohlc(entry_close),
            _flat_ohlc(entry_close),
            _flat_ohlc(entry_close),
        ],
        entry_bar=1,
    )
    frame = frame.with_columns(
        **{EMA_COLUMN: pd.Series([entry_close, entry_close, entry_close - 5.0])},
    )
    trades = simulate_symbol_trades(frame, strategy)

    exit_reason = trades[0].exit_reason
    exit_price = trades[0].exit_price

    print(
        '**summary for close_past_ema short:**\n'
        f'exit_reason = {exit_reason} | exit_price = {exit_price}'
    )

    assert exit_reason == 'close_past_ema'
    assert exit_price == pytest.approx(entry_close)


def test_signal_exit_hit_column_triggers_exit() -> None:
    strategy = _strategy_without_other_exits(load_strategy(STRATEGY_ID))
    entry_close = _fixture_entry_close(strategy)
    frame = _frame_for_sim(
        strategy,
        bar_count=3,
        ohlc_by_bar=[
            _flat_ohlc(entry_close),
            _flat_ohlc(entry_close),
            _flat_ohlc(entry_close),
        ],
        entry_bar=1,
    )
    frame = frame.with_columns(
        **{SIGNAL_EXIT_HIT_COLUMN: pd.Series([False, False, True])},
    )
    trades = simulate_symbol_trades(frame, strategy)

    exit_reason = trades[0].exit_reason
    exit_price = trades[0].exit_price

    print(
        '**summary for signal_exit_hit:**\n'
        f'exit_reason = {exit_reason} | exit_price = {exit_price}'
    )

    assert exit_reason == 'signal_exit'
    assert exit_price == pytest.approx(entry_close)


def _strategy_with_full_allocation(strategy: StrategyConfig) -> StrategyConfig:
    return strategy.model_copy(update={'sizing': SizingFullAllocation(method='full_allocation')})


def test_full_allocation_sizing_uses_capital() -> None:
    base = load_strategy(STRATEGY_ID)
    strategy = _strategy_with_full_allocation(base)
    entry_close = _fixture_entry_close(base)
    capital = 10_000.0
    frame = _frame_for_sim(
        strategy,
        bar_count=2,
        ohlc_by_bar=[_flat_ohlc(entry_close), _flat_ohlc(entry_close)],
        entry_bar=0,
    )
    trades = simulate_symbol_trades(frame, strategy, capital=capital)

    shares = trades[0].shares
    expected_shares = capital / entry_close
    exit_reason = trades[0].exit_reason

    print(
        '**summary for full_allocation sizing:**\n'
        f'shares = {shares} | expected_shares = {expected_shares} | exit_reason = {exit_reason}'
    )

    assert exit_reason == 'end_of_session'
    assert shares == pytest.approx(expected_shares)


def test_full_allocation_sizing_without_capital_raises() -> None:
    base = load_strategy(STRATEGY_ID)
    strategy = _strategy_with_full_allocation(base)
    entry_close = _fixture_entry_close(base)
    frame = _frame_for_sim(
        strategy,
        bar_count=2,
        ohlc_by_bar=[_flat_ohlc(entry_close), _flat_ohlc(entry_close)],
        entry_bar=0,
    )

    with pytest.raises(PortfolioSimError, match='full_allocation'):
        simulate_symbol_trades(frame, strategy)


def test_portfolio_simulator_passes_capital_through() -> None:
    base = load_strategy(STRATEGY_ID)
    strategy = _strategy_with_full_allocation(base)
    entry_close = _fixture_entry_close(base)
    capital = 20_000.0
    frame = _frame_for_sim(
        strategy,
        bar_count=2,
        ohlc_by_bar=[_flat_ohlc(entry_close), _flat_ohlc(entry_close)],
        entry_bar=0,
    ).with_columns(symbol=pd.Series(['AAA', 'AAA']))
    universe = UniverseBarFrames({'AAA': frame})
    result = PortfolioSimulator(strategy, capital=capital).run(universe)

    shares = result.trades[0].shares
    expected_shares = capital / entry_close

    print(
        '**summary for PortfolioSimulator capital passthrough:**\n'
        f'shares = {shares} | expected_shares = {expected_shares}'
    )

    assert shares == pytest.approx(expected_shares)


def test_no_entry_events_returns_no_trades() -> None:
    strategy = load_strategy(STRATEGY_ID)
    entry_close = _fixture_entry_close(strategy)
    frame = frame_with_session(strategy, close=pd.Series([entry_close, entry_close]))
    frame = frame.with_columns(**{ENTRY_EVENT_COLUMN: pd.Series([False, False])})
    trades = simulate_symbol_trades(frame, strategy)

    print(f'**summary for no entries:**\ntrade_count = {len(trades)}')

    assert trades == ()


def test_symbol_from_bars_column_not_frame_metadata() -> None:
    strategy = load_strategy(STRATEGY_ID)
    entry_close = _fixture_entry_close(strategy)
    frame = frame_with_session(strategy, close=pd.Series([entry_close]))
    frame = SymbolBarFrame(
        symbol='WRONG',
        interval_minutes=frame.interval_minutes,
        bars=frame.bars.assign(symbol='AAPL'),
        daily_bars=frame.daily_bars,
        history_bars=frame.history_bars,
    )
    resolved = symbol_from_frame(frame)

    print(f'**summary for symbol_from_frame:**\nresolved = {resolved}')

    assert resolved == 'AAPL'


def test_portfolio_simulator_merges_symbols() -> None:
    strategy = _strategy_without_other_exits(load_strategy(STRATEGY_ID))
    strategy = strategy.model_copy(
        update={
            'take_profit': TakeProfitPctFromEntry(type='pct_from_entry', pct=0.10),
        },
    )
    entry_close = _fixture_entry_close(strategy)
    target_px = take_profit_price_long(entry_close, strategy.take_profit.pct)
    frame_a = _frame_for_sim(
        strategy,
        bar_count=2,
        ohlc_by_bar=[
            _flat_ohlc(entry_close),
            _ohlc_bar(
                open_px=entry_close,
                high_px=target_px + 1,
                low_px=entry_close,
                close_px=entry_close,
            ),
        ],
        entry_bar=0,
    ).with_columns(symbol=pd.Series(['AAA', 'AAA']))
    frame_b = _frame_for_sim(
        strategy,
        bar_count=2,
        ohlc_by_bar=[
            _flat_ohlc(entry_close),
            _ohlc_bar(
                open_px=entry_close,
                high_px=target_px + 1,
                low_px=entry_close,
                close_px=entry_close,
            ),
        ],
        entry_bar=0,
    ).with_columns(symbol=pd.Series(['BBB', 'BBB']))
    universe = UniverseBarFrames({'AAA': frame_a, 'BBB': frame_b})
    result = PortfolioSimulator(strategy).run(universe)

    symbols = {trade.symbol for trade in result.trades}
    trade_count = result.trade_count

    print(
        '**summary for portfolio merge:**\n'
        f'trade_count = {trade_count} | symbols = {sorted(symbols)}'
    )

    assert trade_count == 2
    assert symbols == {'AAA', 'BBB'}
