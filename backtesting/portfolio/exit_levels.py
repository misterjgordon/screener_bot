"""Resolve stop/target prices from strategy exit config and entry fill."""

from dataclasses import dataclass

from backtesting.strategy.strategy_config import OtherExitClosePastEma
from backtesting.strategy.strategy_config import OtherExitEndOfSession
from backtesting.strategy.strategy_config import OtherExitRule
from backtesting.strategy.strategy_config import StopLossConfig
from backtesting.strategy.strategy_config import StopLossPctFromEntry
from backtesting.strategy.strategy_config import StrategySide
from backtesting.strategy.strategy_config import TakeProfitConfig
from backtesting.strategy.strategy_config import TakeProfitPctFromEntry
from strategies.exit.stop_loss.pct_from_entry import stop_loss_price_long
from strategies.exit.stop_loss.pct_from_entry import stop_loss_price_short
from strategies.exit.take_profit.pct_from_entry import take_profit_price_long
from strategies.exit.take_profit.pct_from_entry import take_profit_price_short


@dataclass(frozen=True)
class ExitLevels:
    """Resolved stop/target prices for one entry (either side)."""

    stop_price: float
    take_profit_price: float


def exit_levels(
    entry_price: float,
    stop_loss: StopLossConfig,
    take_profit: TakeProfitConfig,
    side: StrategySide,
) -> ExitLevels:
    """Build stop and target from required YAML ``stop_loss`` / ``take_profit`` blocks."""
    if not isinstance(stop_loss, StopLossPctFromEntry):
        msg = f'Unsupported stop_loss.type: {stop_loss.type!r}'
        raise TypeError(msg)
    if not isinstance(take_profit, TakeProfitPctFromEntry):
        msg = f'Unsupported take_profit.type: {take_profit.type!r}'
        raise TypeError(msg)
    if side == 'long':
        return ExitLevels(
            stop_price=stop_loss_price_long(entry_price, stop_loss.pct),
            take_profit_price=take_profit_price_long(entry_price, take_profit.pct),
        )
    return ExitLevels(
        stop_price=stop_loss_price_short(entry_price, stop_loss.pct),
        take_profit_price=take_profit_price_short(entry_price, take_profit.pct),
    )


def has_end_of_session_other(other_exits: tuple[OtherExitRule, ...]) -> bool:
    """True when ``other_exits`` includes an ``end_of_session`` rule."""
    return any(isinstance(rule, OtherExitEndOfSession) for rule in other_exits)


def close_past_ema_rule(other_exits: tuple[OtherExitRule, ...]) -> OtherExitClosePastEma | None:
    """The ``close_past_ema`` rule in ``other_exits``, if present (at most one supported)."""
    for rule in other_exits:
        if isinstance(rule, OtherExitClosePastEma):
            return rule
    return None
