#!/usr/bin/env python3
"""Per-symbol PM / RTH / AH high and low across the full cold OHLCV lake.

Reads every ``{OHLCV_COLD_ROOT}/1m/{SYMBOL}.parquet`` file in full (no date window), classifies
each bar on America/New_York wall clock (PM < 09:30, RTH 09:30–16:00, AH >= 16:00), and prints
one row per symbol with separate ``pm_*``, ``rth_*``, and ``ah_*`` columns.

Example::

    export OHLCV_COLD_ROOT=/path/to/cold
    uv run --frozen python scripts/inspect_session_hl.py
"""

import sys
from pathlib import Path

_p_repo = Path(__file__).resolve().parent.parent
if str(_p_repo) not in sys.path:
    sys.path.insert(0, str(_p_repo))

import pandas as pd  # noqa: E402

from trading import config as cf  # noqa: E402
from trading.market_timezones import exchange_timezone_name  # noqa: E402
from trading.market_timezones import timestamp_utc_series_to_zone  # noqa: E402
from trading.storage.ohlcv.ohlcv_paths import require_p_ohlcv_cold_root  # noqa: E402
from trading.storage.ohlcv.ohlcv_paths import symbol_path  # noqa: E402
from trading.storage.ohlcv.ohlcv_prepare import validate_and_prepare  # noqa: E402
from trading.storage.ohlcv.ohlcv_schema import OHLCV_COLD_PARQUET_COLUMNS  # noqa: E402

_M_RTH0 = 9 * 60 + 30
_M_AH0 = 16 * 60
_SESSIONS = ('PM', 'RTH', 'AH')
_OUTPUT_COLUMNS = [
    'symbol',
    'pm_high',
    'pm_low',
    'pm_bar_count',
    'rth_high',
    'rth_low',
    'rth_bar_count',
    'ah_high',
    'ah_low',
    'ah_bar_count',
]


def _list_symbols_from_cold_root(p_cold_root: Path) -> list[str]:
    p_1m = p_cold_root / '1m'
    if not p_1m.is_dir():
        return []
    return sorted(p.stem.upper() for p in p_1m.glob('*.parquet') if p.is_file())


def _session_series_from_utc(timestamp_utc: pd.Series) -> pd.Series:
    """Classify UTC timestamps as PM / RTH / AH using ET wall clock."""
    ts_et = timestamp_utc_series_to_zone(timestamp_utc, exchange_timezone_name())
    mins_et = ts_et.dt.hour * 60 + ts_et.dt.minute
    return pd.Series(
        pd.cut(
            mins_et,
            bins=[-1, _M_RTH0, _M_AH0, 24 * 60],
            labels=list(_SESSIONS),
        ),
        index=timestamp_utc.index,
    )


def _read_symbol_bars(sym: str) -> pd.DataFrame:
    p_parquet = symbol_path(sym, interval_minutes=1)
    df_raw = pd.read_parquet(p_parquet, columns=list(OHLCV_COLD_PARQUET_COLUMNS))
    return validate_and_prepare(df_raw)


def _session_hl_row(sym: str, df_bars: pd.DataFrame) -> dict[str, object]:
    df_sess = df_bars.assign(session=_session_series_from_utc(df_bars.timestamp))
    row: dict[str, object] = {'symbol': sym}
    for sess in _SESSIONS:
        key = sess.lower()
        df_part = df_sess.loc[df_sess.session == sess]
        if df_part.empty:
            row[f'{key}_high'] = pd.NA
            row[f'{key}_low'] = pd.NA
            row[f'{key}_bar_count'] = 0
            continue
        row[f'{key}_high'] = float(df_part.high.max())
        row[f'{key}_low'] = float(df_part.low.min())
        row[f'{key}_bar_count'] = int(len(df_part))
    return row


def _build_summary(symbols: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for sym in symbols:
        df_bars = _read_symbol_bars(sym)
        if df_bars.empty:
            continue
        rows.append(_session_hl_row(sym, df_bars))

    if not rows:
        raise SystemExit('No bars in any cold Parquet file')

    df_summary = pd.DataFrame(rows)
    return df_summary.loc[:, _OUTPUT_COLUMNS]


def main() -> None:
    if not cf.OHLCV_COLD_ROOT.strip():
        raise SystemExit('Set OHLCV_COLD_ROOT to the cold OHLCV Parquet root')

    p_cold_root = require_p_ohlcv_cold_root()
    symbols = _list_symbols_from_cold_root(p_cold_root)
    if not symbols:
        raise SystemExit(f'No *.parquet under {p_cold_root / "1m"}')

    summary = _build_summary(symbols)

    print(f'symbols = {len(summary)}')
    print(f'cold_root = {p_cold_root}')
    print()
    print(summary.to_string(index=False))


if __name__ == '__main__':
    main()
