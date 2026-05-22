"""Resolve indicator and condition ids for backtest prep (shared by universe load and inspect)."""

from typing import TYPE_CHECKING

from backtesting.conditions.condition_registry import CONDITION_REGISTRY
from backtesting.indicators.indicator_catalog_load import default_indicator_ids
from backtesting.indicators.indicator_registry import INDICATOR_REGISTRY

if TYPE_CHECKING:
    from backtesting.strategy.strategy_config import StrategyConfig


def resolve_pipeline_indicator_ids(
    strategy: 'StrategyConfig | None',
    *,
    indicator_ids: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Indicator pipeline ids: explicit override, else strategy merge, else catalog defaults."""
    if indicator_ids is not None:
        return indicator_ids
    if strategy is not None:
        return strategy.indicator_ids_for_pipeline(
            default_indicator_ids(),
            frozenset(INDICATOR_REGISTRY.ids()),
        )
    return default_indicator_ids()


def resolve_pipeline_condition_ids(
    strategy: 'StrategyConfig | None',
    *,
    condition_ids: tuple[str, ...] | None = None,
    include_all_registered_conditions: bool = False,
) -> tuple[str, ...]:
    """Condition ids: explicit override, all registry (inspect), strategy list, or none."""
    if condition_ids is not None:
        return condition_ids
    if include_all_registered_conditions:
        return CONDITION_REGISTRY.ids()
    if strategy is not None:
        return strategy.conditions
    return ()
