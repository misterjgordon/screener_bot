"""Universe symbol resolution (explicit, CSV, cold directory)."""

import csv
from pathlib import Path

import pytest

from backtesting.strategy.universe_resolver import list_symbols_from_cold_dir
from backtesting.strategy.universe_resolver import resolve_universe_symbols
from backtesting.strategy.universe_resolver import resolve_universe_symbols_for_backtest
from trading import config as cf

SYMBOL_A = 'AAA'
SYMBOL_B = 'BBB'
SYMBOL_C = 'CCC'


def _write_symbol_list(p_csv: Path, symbols: list[str]) -> None:
    with p_csv.open('w', encoding='utf-8', newline='') as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(['Symbol'])
        for sym in symbols:
            writer.writerow([sym])


def _touch_parquet(p_root: Path, sym: str, *, interval_minutes: int = 1) -> Path:
    p_interval = p_root / f'{interval_minutes}m'
    p_interval.mkdir(parents=True, exist_ok=True)
    p_file = p_interval / f'{sym}.parquet'
    p_file.touch()
    return p_file


def test_resolve_explicit_symbols_normalized() -> None:
    result = resolve_universe_symbols(explicit_symbols=('aapl', 'msft', 'AAPL', ''))

    symbols = result.symbols
    source = result.source

    print(
        '**summary for resolve explicit:**\n'
        f'symbols = {list(symbols)} | source = {source}'
    )

    assert symbols == ('AAPL', 'MSFT')
    assert source == 'explicit'


def test_resolve_symbol_list_from_csv(tmp_path: Path) -> None:
    p_csv = tmp_path / 'tickers.csv'
    _write_symbol_list(p_csv, [SYMBOL_A, SYMBOL_B, SYMBOL_B])

    result = resolve_universe_symbols(p_symbol_list=p_csv)

    print(
        '**summary for resolve symbol list:**\n'
        f'symbols = {list(result.symbols)} | detail = {result.source_detail}'
    )

    assert result.symbols == (SYMBOL_A, SYMBOL_B)
    assert result.source == 'symbol_list'


def test_list_symbols_from_cold_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cf, 'OHLCV_COLD_ROOT', str(tmp_path))
    _touch_parquet(tmp_path, SYMBOL_C)
    _touch_parquet(tmp_path, SYMBOL_A)

    stems = list_symbols_from_cold_dir()

    print(f'**summary for cold dir listing:**\nstems = {list(stems)}')

    assert stems == (SYMBOL_A, SYMBOL_C)


def test_resolve_for_backtest_prefers_explicit_over_cold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cf, 'OHLCV_COLD_ROOT', str(tmp_path))
    _touch_parquet(tmp_path, SYMBOL_A)

    result = resolve_universe_symbols_for_backtest(explicit_symbols=(SYMBOL_B,))

    print(f'**summary for backtest precedence:**\nsymbols = {list(result.symbols)}')

    assert result.symbols == (SYMBOL_B,)
    assert result.source == 'explicit'


def test_resolve_for_backtest_defaults_to_cold_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cf, 'OHLCV_COLD_ROOT', str(tmp_path))
    _touch_parquet(tmp_path, SYMBOL_C)
    _touch_parquet(tmp_path, SYMBOL_A)

    result = resolve_universe_symbols_for_backtest()

    print(
        '**summary for backtest default cold dir:**\n'
        f'symbols = {list(result.symbols)} | source = {result.source}'
    )

    assert result.symbols == (SYMBOL_A, SYMBOL_C)
    assert result.source == 'cold_dir'


def test_resolve_requires_one_source() -> None:
    with pytest.raises(ValueError, match='explicit_symbols'):
        resolve_universe_symbols()
