"""Build the union of tickers from ingested watchlist files for one desk day.

Reads ``gameplan_*.json`` (SMB wrapper: ``payload.stocks[].ticker``) and
``market_rundown_*.txt`` (lines beginning with 1--4 capital letters + whitespace).

Persists ``tickers_on_watchlist_YYYY-MM-DD.json`` under the same
``watchlist/repository/YYYY/MM/DD`` directory when ``save_json`` is True.

shell cmd
uv run --frozen python -m watchlist.tickers_on_watchlist --date 2026-04-08
"""

import argparse
import json
import re
from dataclasses import replace
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from strategies.indicators.atr import atr
from strategies.indicators.gap import gap
from strategies.indicators.percent_of_avg_volume import percent_of_avg_volume
from strategies.utils import RTH_END
from strategies.utils import bar_date
from trading.bar_loader import load_bars
from trading.local_time import local_zone
from trading.market_data import connect
from trading.market_data import disconnect
from trading.models import BarSeries
from trading.models import TickerSummary
from watchlist.sources.smb_gameplan import repository_day_dir

if TYPE_CHECKING:
    from ib_async import IB

SOURCE_SMB_GAMEPLAN = 'smb_gameplan'
SOURCE_MARKET_RUNDOWN = 'market_rundown'
SOURCE_TRADERTV_WATCHLIST = 'tradertv_watchlist'

RUNDOWN_LINE_TICKER = re.compile(r'^([A-Z]{1,4})\s', re.MULTILINE)
TRADERTV_PARENTHESES_TICKER = re.compile(r'\(([A-Z]{1,5})\)')
TRADERTV_BRACKET_TICKER = re.compile(r'\[([A-Z]{1,5})\]')
TRADERTV_WORD_TICKER = re.compile(r'\b[A-Z]{1,5}\b')


def _parse_desk_date(trade_date: date | str) -> date:
    if isinstance(trade_date, date):
        return trade_date
    return date.fromisoformat(str(trade_date))


def _symbols_from_gameplan_file(p_json: Path) -> list[str]:
    """Extract tickers from one saved gameplan wrapper JSON."""
    raw = json.loads(p_json.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        return []
    payload = raw.get('payload')
    if not isinstance(payload, dict):
        return []
    stocks = payload.get('stocks')
    if not isinstance(stocks, list):
        return []
    out: list[str] = []
    for row in stocks:
        if not isinstance(row, dict):
            continue
        t = row.get('ticker')
        if isinstance(t, str) and t.strip():
            out.append(t.strip().upper())
    return out


def _strip_leading_hash_lines(text: str) -> str:
    """Remove ingest header lines that start with ``#`` (e.g. desk_date comment)."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i].strip().startswith('#'):
        i += 1
    return '\n'.join(lines[i:])


def _symbols_from_rundown_text(text: str) -> list[str]:
    """Match tickers at line start: 1--4 ``A-Z`` then whitespace (see ``RUNDOWN_LINE_TICKER``)."""
    body = _strip_leading_hash_lines(text.lstrip('\ufeff'))
    seen: set[str] = set()
    ordered: list[str] = []
    for match in RUNDOWN_LINE_TICKER.finditer(body):
        sym = match.group(1)
        if sym not in seen:
            seen.add(sym)
            ordered.append(sym)
    return ordered


def _symbols_from_tradertv_text(text: str) -> list[str]:
    """Extract TraderTV symbols from Premarket and In-The-News sections."""
    body = _strip_leading_hash_lines(text.lstrip('\ufeff'))

    seen: set[str] = set()
    ordered: list[str] = []

    def add_symbol(sym: str) -> None:
        cleaned = sym.strip().upper()
        if not cleaned:
            return
        if cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)

    idx_pm = body.find('# **Premarket Trading:**')
    if idx_pm != -1:
        idx_earn = body.find('# **Earnings Today:**', idx_pm)
        pm_block = body[idx_pm:idx_earn] if idx_earn != -1 else body[idx_pm:]
        for raw_line in pm_block.splitlines():
            line = raw_line.strip()
            if ':' not in line:
                continue
            rhs = line.split(':', maxsplit=1)[1]
            for sym in TRADERTV_WORD_TICKER.findall(rhs):
                add_symbol(sym)

    idx_news = body.find('# **In The News')
    if idx_news != -1:
        news_block = body[idx_news:]
        for raw_line in news_block.splitlines():
            line = raw_line.strip()
            if line.startswith('## **'):
                for sym in TRADERTV_PARENTHESES_TICKER.findall(line):
                    add_symbol(sym)
                for sym in TRADERTV_BRACKET_TICKER.findall(line):
                    add_symbol(sym)

            if line.startswith('**') and line.endswith('**'):
                inner = line.strip('*').strip()
                if ',' in inner:
                    for sym in TRADERTV_WORD_TICKER.findall(inner):
                        add_symbol(sym)

    return ordered


def _bar_timestamp_as_naive_datetime(bar_dt: object) -> datetime:
    if isinstance(bar_dt, datetime):
        return bar_dt.replace(tzinfo=None) if bar_dt.tzinfo else bar_dt
    raise TypeError(f'bar .date must be datetime, got {type(bar_dt).__name__}')


def _slice_bars_1d_complete(bars_1d: list, eval_dt: datetime) -> list:
    """Daily bars complete as of ``eval_dt``."""
    result = []
    eval_date = eval_dt.date()
    eval_time = eval_dt.time()
    for b in bars_1d:
        bd = bar_date(b.date)
        if bd is None:
            continue
        if bd < eval_date or bd == eval_date and eval_time >= RTH_END:
            result.append(b)
    return result


def _slice_bars_2min_through(bars_2min: list, eval_dt: datetime) -> list:
    out = []
    for b in bars_2min:
        dt = _bar_timestamp_as_naive_datetime(b.date)
        if dt <= eval_dt:
            out.append(b)
    return out


def _eval_as_of_pct_volume(sliced_2min: list, desk_date: date) -> datetime:
    """Naive-ET instant for :func:`percent_of_avg_volume` on ``desk_date``.

    Clamps to the latest 2m bar through RTH close so pre-open runs use section 1
    (D0 AH + D1 pre) through the last bar instead of evaluating at 16:00 ET with
    no RTH data yet.
    """
    close_et = datetime.combine(desk_date, RTH_END)
    if not sliced_2min:
        return close_et
    last_ts = max(_bar_timestamp_as_naive_datetime(b.date) for b in sliced_2min)
    return min(close_et, last_ts)


def _save_watchlist_json(p_day_dir: Path, desk_date: date, rows: list[TickerSummary]) -> Path:
    """Write ``tickers_on_watchlist_YYYY-MM-DD.json``."""
    name = f'tickers_on_watchlist_{desk_date.isoformat()}.json'
    p_out = p_day_dir / name
    payload = {
        'trade_date': desk_date.isoformat(),
        'generated_at_pt': datetime.now(local_zone()).isoformat(),
        'tickers': [
            {
                'symbol': row.symbol,
                'source_id': row.source_id,
                'trade_date': row.trade_date.isoformat(),
                'atr_14': row.atr_14,
                'percent_of_avg_volume': row.percent_of_avg_volume,
                'gap_percent': row.gap_percent,
                'gap_atr': row.gap_atr,
            }
            for row in rows
        ],
    }
    p_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return p_out


def _ticker_summaries_with_ib_technicals(
    ib: 'IB',
    rows: list[TickerSummary],
    desk_date: date,
) -> list[TickerSummary]:
    """Fill ``atr_14``, ``percent_of_avg_volume``, ``gap_percent``, and ``gap_atr``.

    One :func:`trading.bar_loader.load_bars` call per distinct symbol (daily + 2m).
    Percent-of-average and gap use :func:`_eval_as_of_pct_volume` (last 2m bar through ET
    session close on ``desk_date``) so pre-open data maps to section 1 and RTH to section 2.
    """
    atr_by_symbol: dict[str, float | None] = {}
    pct_by_symbol: dict[str, int | None] = {}
    gap_pct_by_symbol: dict[str, float | None] = {}
    gap_atr_by_symbol: dict[str, float | None] = {}
    eval_close_et = datetime.combine(desk_date, RTH_END)
    for sym in sorted({r.symbol for r in rows}):
        bundle = load_bars(ib, sym, end_date=desk_date)
        if not bundle:
            atr_by_symbol[sym] = None
            pct_by_symbol[sym] = None
            gap_pct_by_symbol[sym] = None
            gap_atr_by_symbol[sym] = None
            continue
        atr_by_symbol[sym] = atr(bundle.bars_1d, period=14)
        sliced_1d = _slice_bars_1d_complete(bundle.bars_1d, eval_close_et)
        sliced_2min = _slice_bars_2min_through(bundle.bars_2min, eval_close_et)
        if not sliced_1d:
            pct_by_symbol[sym] = None
            gap_pct_by_symbol[sym] = None
            gap_atr_by_symbol[sym] = None
            continue
        series = BarSeries(bars_1d=sliced_1d, bars_2min=sliced_2min)
        eval_pct = _eval_as_of_pct_volume(sliced_2min, desk_date)
        poc = percent_of_avg_volume(series, eval_as_of=eval_pct)
        if poc is None or poc.percent_of_average is None:
            pct_by_symbol[sym] = None
        else:
            pct_by_symbol[sym] = int(round(poc.percent_of_average))
        gap_row = gap(series, eval_as_of=eval_pct)
        if gap_row is None:
            gap_pct_by_symbol[sym] = None
            gap_atr_by_symbol[sym] = None
        else:
            gap_pct_by_symbol[sym] = gap_row.gap_percent
            gap_atr_by_symbol[sym] = gap_row.gap_atr
    return [
        replace(
            r,
            atr_14=atr_by_symbol[r.symbol],
            percent_of_avg_volume=pct_by_symbol[r.symbol],
            gap_percent=gap_pct_by_symbol[r.symbol],
            gap_atr=gap_atr_by_symbol[r.symbol],
        )
        for r in rows
    ]


def tickers_on_watchlist(
    trade_date: date | str,
    *,
    repository_dir: Path | None = None,
    save_json: bool = True,
    ib: 'IB | None' = None,
    fetch_atr_14: bool = True,
) -> list[TickerSummary]:
    """Load ingested sources for ``trade_date`` and return deduped :class:`TickerSummary` rows.

    ``source_id`` is the ingest module basename (``smb_gameplan``, ``market_rundown``).

    Parameters
    ----------
    trade_date
        Desk calendar day (directory under ``watchlist/repository``).
    repository_dir
        Override repository day folder (for tests).
    save_json
        If True, write ``tickers_on_watchlist_<date>.json`` under that folder.
    ib
        Connected IB client. When set with ``fetch_atr_14``, :func:`trading.bar_loader.load_bars`
        runs per distinct symbol; :func:`~strategies.indicators.atr.atr`,
        :func:`~strategies.indicators.percent_of_avg_volume.percent_of_avg_volume`, and
        :func:`~strategies.indicators.gap.gap` fill ``atr_14``, ``percent_of_avg_volume``,
        ``gap_percent``, and ``gap_atr`` (evaluation through last 2m bar vs ET session close).
    fetch_atr_14
        If False, skip IB requests (technicals fields stay None).
    """
    desk_date = _parse_desk_date(trade_date)
    p_day = repository_dir if repository_dir is not None else repository_day_dir(desk_date)

    by_key: dict[tuple[str, str], TickerSummary] = {}

    if p_day.is_dir():
        for p_gp in sorted(p_day.glob('gameplan_*.json')):
            for sym in _symbols_from_gameplan_file(p_gp):
                key = (sym, SOURCE_SMB_GAMEPLAN)
                if key not in by_key:
                    by_key[key] = TickerSummary(
                        symbol=sym,
                        source_id=SOURCE_SMB_GAMEPLAN,
                        trade_date=desk_date,
                    )

        for p_rd in sorted(p_day.glob('market_rundown_*.txt')):
            text = p_rd.read_text(encoding='utf-8')
            for sym in _symbols_from_rundown_text(text):
                key = (sym, SOURCE_MARKET_RUNDOWN)
                if key not in by_key:
                    by_key[key] = TickerSummary(
                        symbol=sym,
                        source_id=SOURCE_MARKET_RUNDOWN,
                        trade_date=desk_date,
                    )

        for p_ttv in sorted(p_day.glob('trader_tv_*.txt')):
            text = p_ttv.read_text(encoding='utf-8')
            for sym in _symbols_from_tradertv_text(text):
                key = (sym, SOURCE_TRADERTV_WATCHLIST)
                if key not in by_key:
                    by_key[key] = TickerSummary(
                        symbol=sym,
                        source_id=SOURCE_TRADERTV_WATCHLIST,
                        trade_date=desk_date,
                    )

    rows = sorted(by_key.values(), key=lambda r: (r.symbol, r.source_id))

    if (
        fetch_atr_14
        and ib is not None
        and ib.isConnected()
        and rows
    ):
        rows = _ticker_summaries_with_ib_technicals(ib, rows, desk_date)

    if save_json:
        p_day.mkdir(parents=True, exist_ok=True)
        _save_watchlist_json(p_day, desk_date, rows)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Union tickers from ingested watchlist files for one desk day.',
    )
    parser.add_argument(
        '--date',
        metavar='YYYY-MM-DD',
        help='Desk date (default: today in local time).',
    )
    args = parser.parse_args()
    desk = date.fromisoformat(args.date) if args.date else date.today()
    ib = connect(readonly=True)
    try:
        rows = tickers_on_watchlist(desk, ib=ib)
    finally:
        disconnect(ib)
    with_atr = sum(1 for r in rows if r.atr_14 is not None)
    with_pct = sum(1 for r in rows if r.percent_of_avg_volume is not None)
    with_gap = sum(1 for r in rows if r.gap_percent is not None)
    print(
        f'date={desk.isoformat()} count={len(rows)} '
        f'with_atr_14={with_atr} with_percent_of_avg_volume={with_pct} with_gap_percent={with_gap}'
    )
    for row in rows:
        print(f'{row.symbol} {row.source_id}')


if __name__ == '__main__':
    main()
