"""Run portfolio simulation across a prepped :class:`~backtesting.frames.universe_bar_frames.UniverseBarFrames`."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backtesting.portfolio.symbol_simulator import simulate_symbol_trades

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.frames.universe_bar_frames import UniverseBarFrames
    from backtesting.portfolio.trade import Trade
    from backtesting.strategy.strategy_config import StrategyConfig


@dataclass(frozen=True)
class PortfolioSimResult:
    """Aggregate trades and PnL after simulating every symbol in the universe."""

    trades: tuple['Trade', ...]

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def total_pnl(self) -> float:
        return sum(trade.pnl for trade in self.trades)

    def trades_for_symbol(self, symbol: str) -> tuple['Trade', ...]:
        """Trades for one ticker (uppercase-normalised)."""
        key = symbol.strip().upper()
        return tuple(trade for trade in self.trades if trade.symbol == key)


class PortfolioSimulator:
    """Bar-table → trade list using ``entry_event`` and strategy exit/sizing rules.

    Expects each frame to already have run IndicatorPipeline, ConditionPipeline
    (session columns), and SignalPipeline. Fill model: long entry at bar close;
    stops/targets from entry fill; ``end_of_session`` at last RTH bar close.
    """

    def __init__(self, strategy: 'StrategyConfig') -> None:
        self._strategy = strategy

    @property
    def strategy(self) -> 'StrategyConfig':
        return self._strategy

    def run_symbol(self, frame: 'SymbolBarFrame') -> tuple['Trade', ...]:
        """Simulate one symbol's prepared bar table."""
        return simulate_symbol_trades(frame, self._strategy)

    def run(self, universe: 'UniverseBarFrames') -> PortfolioSimResult:
        """Simulate each loaded frame's bars and return trades sorted by entry time."""
        all_trades: list[Trade] = []
        for frame in universe.iter_frames():
            all_trades.extend(self.run_symbol(frame))
        all_trades.sort(key=lambda trade: trade.entry_timestamp_utc)
        return PortfolioSimResult(trades=tuple(all_trades))
