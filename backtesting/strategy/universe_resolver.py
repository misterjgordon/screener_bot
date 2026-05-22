"""Resolve symbol lists for portfolio backtests (cold dir, CSV, or explicit tickers)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backtesting.bt_config import DEFAULT_INTERVAL_MINUTES
from trading.storage.ohlcv.ohlcv_paths import get_p_ohlcv_symbol_list_path
from trading.storage.ohlcv.ohlcv_paths import load_tickers_from_symbol_list_file
from trading.storage.ohlcv.ohlcv_paths import require_p_ohlcv_cold_root

UniverseSourceKind = Literal['explicit', 'symbol_list', 'cold_dir']


@dataclass(frozen=True)
class UniverseResolveResult:
    """Symbols requested for a run and how they were chosen."""

    symbols: tuple[str, ...]
    source: UniverseSourceKind
    source_detail: str


def _normalize_symbol_list(symbols: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in symbols:
        sym = raw.strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        ordered.append(sym)
    return tuple(ordered)


def list_symbols_from_cold_dir(*, interval_minutes: int = DEFAULT_INTERVAL_MINUTES) -> tuple[str, ...]:
    """Sorted ``*.parquet`` stems under ``{OHLCV_COLD_ROOT}/{interval}m/`` (no Parquet read)."""
    p_root = require_p_ohlcv_cold_root()
    p_interval = p_root / f'{interval_minutes}m'
    if not p_interval.is_dir():
        msg = f'Cold interval directory not found: {p_interval}'
        raise FileNotFoundError(msg)
    stems = sorted(p.stem.upper() for p in p_interval.glob('*.parquet') if p.is_file())
    if not stems:
        msg = f'No *.parquet under {p_interval}'
        raise FileNotFoundError(msg)
    return tuple(stems)


def resolve_universe_symbols(
    *,
    explicit_symbols: tuple[str, ...] | None = None,
    p_symbol_list: Path | None = None,
    use_cold_dir: bool = False,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
) -> UniverseResolveResult:
    """Resolve symbols from exactly one source (raises if none is provided).

    Precedence when multiple flags are set: ``explicit_symbols`` → ``p_symbol_list`` →
    ``use_cold_dir``.
    """
    if explicit_symbols:
        symbols = _normalize_symbol_list(explicit_symbols)
        if not symbols:
            msg = 'explicit_symbols is empty after normalization'
            raise ValueError(msg)
        return UniverseResolveResult(symbols, 'explicit', 'explicit_symbols')

    if p_symbol_list is not None:
        p_resolved = p_symbol_list.expanduser().resolve()
        tickers = tuple(load_tickers_from_symbol_list_file(p_resolved))
        return UniverseResolveResult(
            tickers,
            'symbol_list',
            str(p_resolved),
        )

    if use_cold_dir:
        stems = list_symbols_from_cold_dir(interval_minutes=interval_minutes)
        return UniverseResolveResult(stems, 'cold_dir', f'{interval_minutes}m/*.parquet')

    msg = 'Provide explicit_symbols, p_symbol_list, or use_cold_dir=True'
    raise ValueError(msg)


def resolve_universe_symbols_for_backtest(
    *,
    explicit_symbols: tuple[str, ...] | None = None,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
) -> UniverseResolveResult:
    """Backtest default universe: explicit tickers, else all cold ``{interval}m/*.parquet`` stems."""
    if explicit_symbols:
        return resolve_universe_symbols(
            explicit_symbols=explicit_symbols,
            interval_minutes=interval_minutes,
        )
    return resolve_universe_symbols(use_cold_dir=True, interval_minutes=interval_minutes)


def resolve_universe_symbols_default_shortlist(
    *,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
) -> UniverseResolveResult:
    """Symbols from the repo shortlist CSV (or ``OHLCV_SYMBOL_LIST_PATH``)."""
    p_list = get_p_ohlcv_symbol_list_path()
    return resolve_universe_symbols(p_symbol_list=p_list, interval_minutes=interval_minutes)
