"""Load :class:`~backtesting.strategy.strategy_config.StrategyConfig` from YAML files."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from backtesting.strategy.strategy_config import StrategyConfig

_p_backtesting = Path(__file__).resolve().parent.parent
P_STRATEGY_CONFIGS_DIR = _p_backtesting.parent / 'strategies' / 'configs'


class StrategyConfigLoadError(ValueError):
    """Raised when YAML is missing, invalid, or fails model validation."""


def resolve_strategy_config_path(strategy_id_or_path: str) -> Path:
    """Resolve ``ema_cross`` → ``strategies/configs/ema_cross.yaml`` or pass through a file path."""
    raw = strategy_id_or_path.strip()
    p_candidate = Path(raw).expanduser()
    if p_candidate.suffix in ('.yaml', '.yml'):
        return p_candidate.resolve()
    return (P_STRATEGY_CONFIGS_DIR / f'{raw}.yaml').resolve()


def load_strategy_config(strategy_id_or_path: str) -> StrategyConfig:
    """Load and validate one strategy YAML into :class:`StrategyConfig`."""
    p_config = resolve_strategy_config_path(strategy_id_or_path)
    if not p_config.is_file():
        msg = f'Strategy config not found: {p_config}'
        raise StrategyConfigLoadError(msg)

    with p_config.open(encoding='utf-8') as yaml_file:
        raw = yaml.safe_load(yaml_file)

    if not isinstance(raw, dict):
        msg = f'Expected mapping at root of {p_config.name}, got {type(raw).__name__}'
        raise StrategyConfigLoadError(msg)

    try:
        return StrategyConfig.model_validate(raw)
    except ValidationError as exc:
        msg = f'Invalid strategy config {p_config.name}: {exc}'
        raise StrategyConfigLoadError(msg) from exc
