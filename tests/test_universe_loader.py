"""Universe load orchestrator: skip missing Parquet, run pipelines on successes."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backtesting.conditions.session_regime import SESSION_COLUMN
from backtesting.conditions.session_regime import SIGNAL_ELIGIBLE_COLUMN
from backtesting.frames.symbol_bar_frame import SymbolBarFrame
from backtesting.io.cold_bar_source import ColdBarSource
from backtesting.signals.entry_columns import ENTRY_EVENT_COLUMN
from backtesting.signals.signal_columns import trigger_column_name
from backtesting.universe.universe_loader import format_universe_load_report
from backtesting.universe.universe_loader import load_universe_bars
from tests.strategy_signal_test_support import discover_strategy_ids
from tests.strategy_signal_test_support import load_strategy
from tests.strategy_signal_test_support import minimal_ohlcv_bars
from tests.strategy_signal_test_support import minimal_strategy_bars
from trading import config as cf
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_END_DATE
from trading.storage.ohlcv.ohlcv_ingest_limits import OHLCV_DEFAULT_INGEST_START_DATE
from trading.storage.ohlcv.ohlcv_paths import get_p_ohlcv_symbol_list_path
from trading.storage.ohlcv.ohlcv_paths import load_tickers_from_symbol_list_file
from trading.storage.ohlcv.ohlcv_paths import symbol_path

SYMBOL_HAVE = 'AAA'
SYMBOL_MISSING = 'ZZZ'
SESSION_DATE = date(2026, 5, 15)
STRATEGY_IDS = discover_strategy_ids()


def _empty_frame(sym: str) -> SymbolBarFrame:
    return SymbolBarFrame(
        symbol=sym,
        interval_minutes=1,
        bars=minimal_ohlcv_bars(sym).iloc[0:0],
        daily_bars=pd.DataFrame(),
        history_bars=pd.DataFrame(),
    )


class _StubColdBarSource:
    """Minimal source for loader tests without Parquet."""

    def __init__(
        self,
        *,
        start: date,
        end: date,
        frames_by_symbol: dict[str, SymbolBarFrame],
        interval_minutes: int = 1,
    ) -> None:
        self.start = start
        self.end = end
        self.interval_minutes = interval_minutes
        self._frames = frames_by_symbol

    def load(self, symbol: str) -> SymbolBarFrame:
        sym = symbol.strip().upper()
        if sym not in self._frames:
            msg = f'No frame for {sym}'
            raise FileNotFoundError(msg)
        return self._frames[sym]


def test_load_universe_skips_missing_parquet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cf, 'OHLCV_COLD_ROOT', str(tmp_path))
    p_have = tmp_path / '1m' / f'{SYMBOL_HAVE}.parquet'
    p_have.parent.mkdir(parents=True)
    p_have.touch()

    source = _StubColdBarSource(
        start=SESSION_DATE,
        end=SESSION_DATE,
        frames_by_symbol={
            SYMBOL_HAVE: SymbolBarFrame(
                symbol=SYMBOL_HAVE,
                interval_minutes=1,
                bars=minimal_ohlcv_bars(SYMBOL_HAVE),
                daily_bars=pd.DataFrame(),
                history_bars=pd.DataFrame(),
            ),
        },
    )

    universe, report = load_universe_bars(
        [SYMBOL_HAVE, SYMBOL_MISSING],
        source,
        indicator_ids=('trading_date',),
    )

    has_missing_skip = SYMBOL_MISSING in report.skipped_no_parquet
    loaded_ok = universe.symbols == [SYMBOL_HAVE]
    messages_contain_skip = any(SYMBOL_MISSING in msg for msg in report.messages)

    print(
        '**summary for skip missing parquet:**\n'
        f'loaded = {universe.symbols}\n'
        f'skipped_no_parquet = {list(report.skipped_no_parquet)}\n'
        f'{format_universe_load_report(report)}'
    )

    assert has_missing_skip
    assert loaded_ok
    assert messages_contain_skip
    assert 'trading_date' in universe.get(SYMBOL_HAVE).column_names


def test_load_universe_skips_empty_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cf, 'OHLCV_COLD_ROOT', str(tmp_path))
    p_file = tmp_path / '1m' / f'{SYMBOL_HAVE}.parquet'
    p_file.parent.mkdir(parents=True)
    p_file.touch()

    source = _StubColdBarSource(
        start=SESSION_DATE,
        end=SESSION_DATE,
        frames_by_symbol={SYMBOL_HAVE: _empty_frame(SYMBOL_HAVE)},
    )

    universe, report = load_universe_bars([SYMBOL_HAVE], source, indicator_ids=('trading_date',))

    empty_skipped = SYMBOL_HAVE in report.skipped_empty_window
    no_loaded = len(universe) == 0

    print(
        '**summary for skip empty window:**\n'
        f'skipped_empty_window = {list(report.skipped_empty_window)}\n'
        f'loaded_count = {report.loaded_count}'
    )

    assert empty_skipped
    assert no_loaded


@pytest.mark.parametrize('strategy_id', STRATEGY_IDS)
def test_load_universe_with_strategy_columns(
    strategy_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cf, 'OHLCV_COLD_ROOT', str(tmp_path))
    p_file = tmp_path / '1m' / f'{SYMBOL_HAVE}.parquet'
    p_file.parent.mkdir(parents=True)
    p_file.touch()

    strategy = load_strategy(strategy_id)
    source = _StubColdBarSource(
        start=SESSION_DATE,
        end=SESSION_DATE,
        frames_by_symbol={
            SYMBOL_HAVE: SymbolBarFrame(
                symbol=SYMBOL_HAVE,
                interval_minutes=1,
                bars=minimal_strategy_bars(strategy, SYMBOL_HAVE),
                daily_bars=pd.DataFrame(),
                history_bars=pd.DataFrame(),
            ),
        },
    )

    universe, report = load_universe_bars(
        [SYMBOL_HAVE],
        source,
        strategy=strategy,
        indicator_ids=(),
    )
    frame = universe.get(SYMBOL_HAVE)
    expected_triggers = [trigger_column_name(t.id) for t in strategy.triggers]
    has_session = SESSION_COLUMN in frame.column_names
    has_eligible = SIGNAL_ELIGIBLE_COLUMN in frame.column_names
    triggers_present = all(col in frame.column_names for col in expected_triggers)
    has_entry_event = ENTRY_EVENT_COLUMN in frame.column_names

    print(
        f'**summary for universe strategy columns ({strategy_id}):**\n'
        f'has_session = {has_session} | has_signal_eligible = {has_eligible}\n'
        f'triggers_present = {triggers_present} | has_entry_event = {has_entry_event}\n'
        f'loaded_count = {report.loaded_count}'
    )

    assert has_session
    assert has_eligible
    assert triggers_present
    assert has_entry_event
    assert report.loaded_count == 1


def _require_cold_integration_symbols() -> list[str]:
    if not cf.OHLCV_COLD_ROOT.strip():
        pytest.skip('Set OHLCV_COLD_ROOT for cold integration test')

    tickers = load_tickers_from_symbol_list_file(get_p_ohlcv_symbol_list_path())
    have = [s for s in tickers[:5] if symbol_path(s).is_file()]
    missing = 'NOTINLAKE999'
    if not have:
        pytest.skip('No shortlist symbols with cold Parquet')
    return have + [missing]


def test_load_universe_cold_shortlist_continues_on_missing() -> None:
    """CSV may list symbols without Parquet; loader skips them and loads the rest."""
    symbols = _require_cold_integration_symbols()
    source = ColdBarSource(
        OHLCV_DEFAULT_INGEST_START_DATE,
        OHLCV_DEFAULT_INGEST_END_DATE,
        warmup_bars=0,
    )
    strategy = load_strategy(STRATEGY_IDS[0])

    universe, report = load_universe_bars(symbols, source, strategy=strategy)

    loaded_at_least_one = report.loaded_count >= 1
    skipped_missing = 'NOTINLAKE999' in report.skipped_no_parquet
    all_requested = len(report.requested_symbols) == len(symbols)

    print(
        '**summary for cold shortlist load:**\n'
        f'{format_universe_load_report(report)}\n'
        f'loaded_at_least_one = {loaded_at_least_one}'
    )

    assert all_requested
    assert loaded_at_least_one
    assert skipped_missing
    assert len(universe) == report.loaded_count
