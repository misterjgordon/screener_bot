"""Capital allocation across a strategy's merged, cross-symbol entry-candidate stream.

Entry detection (which bars are ``entry_event`` rows) is unaffected by capital — the same
candidate set feeds every allocation mode. What differs is which candidates become trades:

- **Uncapped** — every candidate trades at full ``capital``, independent of any other open
  position (see :class:`~backtesting.portfolio.portfolio_simulator.PortfolioSimulator`, which
  already simulates each symbol independently). Deliberately unrealistic when positions
  overlap — it is the diagnostic for how much capital contention a strategy would face.
- **Single position** — one open position for the whole strategy across every symbol; a
  candidate is accepted only once the previous one has exited. Everything else is skipped
  and reported, not silently dropped.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backtesting.portfolio.candidate_merge import EntryCandidate
from backtesting.portfolio.candidate_merge import collect_entry_candidates
from backtesting.portfolio.symbol_simulator import SymbolExitContext
from backtesting.portfolio.symbol_simulator import build_symbol_exit_context
from backtesting.portfolio.symbol_simulator import resolve_trade_for_candidate

if TYPE_CHECKING:
    from backtesting.frames.universe_bar_frames import UniverseBarFrames
    from backtesting.portfolio.trade import Trade
    from backtesting.strategy.strategy_config import StrategyConfig


@dataclass(frozen=True)
class CapitalAllocationResult:
    """Trades accepted under one allocation mode, plus candidates it could not take."""

    trades: tuple['Trade', ...]
    skipped: tuple[EntryCandidate, ...]


def simulate_single_position(
    universe: 'UniverseBarFrames',
    strategy: 'StrategyConfig',
    *,
    capital: float,
) -> CapitalAllocationResult:
    """One open position for the whole strategy across every symbol; later entries wait.

    Walks entry candidates from every symbol in chronological order. A candidate is
    accepted only if its entry is at or after the previous accepted trade's exit; on
    accept, its exit is resolved immediately via the same per-symbol exit dispatcher
    used by the uncapped path, and that exit timestamp becomes the earliest the next
    candidate may be accepted.
    """
    candidates = collect_entry_candidates(universe, strategy)
    contexts: dict[str, SymbolExitContext] = {}

    trades: list[Trade] = []
    skipped: list[EntryCandidate] = []
    next_available_ts = None

    for candidate in candidates:
        if next_available_ts is not None and candidate.entry_timestamp_utc < next_available_ts:
            skipped.append(candidate)
            continue

        ctx = contexts.get(candidate.symbol)
        if ctx is None:
            ctx = build_symbol_exit_context(candidate.frame, strategy)
            contexts[candidate.symbol] = ctx

        trade = resolve_trade_for_candidate(ctx, candidate.entry_pos, strategy, capital=capital)
        trades.append(trade)
        next_available_ts = trade.exit_timestamp_utc

    return CapitalAllocationResult(trades=tuple(trades), skipped=tuple(skipped))
