"""Merge entry candidates from every symbol in a universe into one chronological stream."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backtesting.portfolio.symbol_simulator import entry_candidate_positions
from backtesting.portfolio.symbol_simulator import symbol_from_frame

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame
    from backtesting.frames.universe_bar_frames import UniverseBarFrames
    from backtesting.strategy.strategy_config import StrategyConfig


@dataclass(frozen=True)
class EntryCandidate:
    """One ``entry_event`` row, not yet resolved into a trade or accepted/skipped."""

    symbol: str
    frame: 'SymbolBarFrame'
    entry_pos: int
    entry_timestamp_utc: object


def collect_entry_candidates(
    universe: 'UniverseBarFrames',
    strategy: 'StrategyConfig',
) -> tuple[EntryCandidate, ...]:
    """Every symbol's entry candidates, merged and sorted by ``entry_timestamp_utc``.

    Entry detection is capital-agnostic (unaffected by allocation mode) — this is the
    same candidate set every allocator (uncapped or single-position) chooses from.
    """
    candidates: list[EntryCandidate] = []
    for frame in universe.iter_frames():
        bars = frame.bars
        if bars.empty:
            continue
        symbol = symbol_from_frame(frame)
        for pos in entry_candidate_positions(bars):
            candidates.append(
                EntryCandidate(
                    symbol=symbol,
                    frame=frame,
                    entry_pos=pos,
                    entry_timestamp_utc=bars.iloc[pos].timestamp,
                ),
            )
    candidates.sort(key=lambda candidate: candidate.entry_timestamp_utc)
    return tuple(candidates)
