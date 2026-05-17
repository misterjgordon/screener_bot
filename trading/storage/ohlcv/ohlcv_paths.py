"""Cold-store paths and ticker-list file resolution."""

import csv
from pathlib import Path

from trading import config as cf

_p_trading_pkg = Path(__file__).resolve().parent.parent.parent
p_ohlcv_symbol_list_path = _p_trading_pkg / 'data' / 'symbols' / 'shortlist_stocks.csv'


def get_p_ohlcv_symbol_list_path() -> Path:
    """Return ticker list file path: env ``OHLCV_SYMBOL_LIST_PATH`` or default shortlist."""
    raw = cf.OHLCV_SYMBOL_LIST_PATH.strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return p_ohlcv_symbol_list_path.resolve()


def load_tickers_from_symbol_list_file(p_csv: Path) -> list[str]:
    """First column per row; skip a leading ``Symbol`` header row; dedupe preserving order."""
    if not p_csv.is_file():
        msg = f'Symbol list file not found: {p_csv}'
        raise FileNotFoundError(msg)

    with p_csv.open(encoding='utf-8-sig', newline='') as csv_file:
        rows = list(csv.reader(csv_file))

    if not rows:
        msg = f'Symbol list file is empty: {p_csv}'
        raise ValueError(msg)

    start = 0
    first_cell = rows[0][0].strip().upper() if rows[0] else ''
    if first_cell == 'SYMBOL':
        start = 1

    tickers: list[str] = []
    seen: set[str] = set()
    for row in rows[start:]:
        if not row:
            continue
        sym = row[0].strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        tickers.append(sym)

    if not tickers:
        msg = f'No tickers parsed from symbol list file: {p_csv}'
        raise ValueError(msg)

    return tickers


def require_p_ohlcv_cold_root() -> Path:
    """Return resolved cold root or raise if unset."""
    if not cf.OHLCV_COLD_ROOT:
        msg = 'Set OHLCV_COLD_ROOT to the cold OHLCV Parquet root directory'
        raise ValueError(msg)
    return Path(cf.OHLCV_COLD_ROOT).expanduser().resolve()


def symbol_path(symbol: str, *, interval_minutes: int = 1) -> Path:
    """Per-symbol Parquet: ``{root}/{interval}m/{SYMBOL}.parquet``.

    All minute bars for one ticker live in one file (merge on write). Jambot often
    inserts a ``us_equities`` segment under ``{interval}m/``; here the cold root
    already scopes asset class (e.g. ``.../Equities``), so that segment is omitted.
    """
    p_root = require_p_ohlcv_cold_root()
    return p_root / f'{interval_minutes}m' / f'{symbol.strip().upper()}.parquet'
