"""Desk session ranges (PT windows) for each distinct watchlist symbol, saved as JSON.

Reads the same ticker union as :func:`watchlist.tickers_on_watchlist.tickers_on_watchlist`,
loads 2m bars with :data:`strategies.indicators.session_range.BARS_2MIN_DURATION_FOR_DESK_SESSION_RANGES`,
and writes ``session_range_YYYY_MM_DD.json`` under ``watchlist/repository/YYYY/MM/DD/``.

shell cmd
uv run --frozen python -m watchlist.session_range_export --date 2026-04-23
"""

import argparse
import json
from dataclasses import asdict
from datetime import date
from datetime import datetime
from datetime import time
from pathlib import Path
from typing import TYPE_CHECKING
from typing import TypedDict
from typing import cast

from strategies.indicators.adr import calculate_adr
from strategies.indicators.session_range import BARS_2MIN_DURATION_FOR_DESK_SESSION_RANGES
from strategies.indicators.session_range import DeskSessionRanges
from strategies.indicators.session_range import compute_desk_session_ranges
from strategies.utils import RTH_END
from trading.bar_loader import load_bars
from trading.local_time import local_wall_to_naive_et
from trading.local_time import local_zone
from trading.market_data import connect
from trading.market_data import disconnect
from trading.models import BarSeries
from watchlist.sources.smb_gameplan import repository_day_dir
from watchlist.tickers_on_watchlist import WATCHLIST_IB_CLIENT_ID
from watchlist.tickers_on_watchlist import _eval_as_of_pct_volume
from watchlist.tickers_on_watchlist import _slice_bars_1d_complete
from watchlist.tickers_on_watchlist import _slice_bars_2min_through
from watchlist.tickers_on_watchlist import tickers_on_watchlist

if TYPE_CHECKING:
    from ib_async import IB


class SessionOhlcAdrJson(TypedDict):
    """JSON-ready OHLC plus ``change`` and ``adr_change_percent`` as plain numbers."""

    open: float | None
    high: float | None
    low: float | None
    close: float | None
    change: float | None
    adr_change_percent: float | None


class DeskSessionsJson(TypedDict):
    """Six desk windows for JSON export."""

    prior_day_ah_session: SessionOhlcAdrJson
    pm_session: SessionOhlcAdrJson
    opening_range_session: SessionOhlcAdrJson
    morning_session: SessionOhlcAdrJson
    afternoon_session: SessionOhlcAdrJson
    closing_session: SessionOhlcAdrJson


class SessionRangeTickerRow(TypedDict):
    """One symbol row in the export file."""

    symbol: str
    adr_20: float | None
    sessions: DeskSessionsJson | None


class SessionRangeExportPayload(TypedDict):
    """Top-level JSON object written for one desk day."""

    trade_date: str
    generated_at_pt: str
    eval_as_of_et: str | None
    tickers: list[SessionRangeTickerRow]


def _parse_desk_date(trade_date: date | str) -> date:
    if isinstance(trade_date, date):
        return trade_date
    return date.fromisoformat(str(trade_date))


def desk_session_ranges_to_dict(ranges: DeskSessionRanges) -> DeskSessionsJson:
    """Serialize :class:`~strategies.indicators.session_range.DeskSessionRanges` for JSON."""
    return cast(
        'DeskSessionsJson',
        {
            'prior_day_ah_session': asdict(ranges.prior_day_ah_session),
            'pm_session': asdict(ranges.pm_session),
            'opening_range_session': asdict(ranges.opening_range_session),
            'morning_session': asdict(ranges.morning_session),
            'afternoon_session': asdict(ranges.afternoon_session),
            'closing_session': asdict(ranges.closing_session),
        },
    )


def _slice_for_eval(
    desk_date: date,
    bundle: 'BarSeries',
    eval_time_local: time | None,
) -> tuple[list, list, datetime]:
    """Return ``(sliced_1d, sliced_2min, eval_as_of_et)`` for ADR and session aggregation."""
    if eval_time_local is not None:
        eval_dt_et = local_wall_to_naive_et(desk_date, eval_time_local)
        sliced_1d = _slice_bars_1d_complete(bundle.bars_1d, eval_dt_et)
        sliced_2min = _slice_bars_2min_through(bundle.bars_2min, eval_dt_et)
        return (sliced_1d, sliced_2min, eval_dt_et)
    eval_close_et = datetime.combine(desk_date, RTH_END)
    sliced_1d = _slice_bars_1d_complete(bundle.bars_1d, eval_close_et)
    sliced_2min = _slice_bars_2min_through(bundle.bars_2min, eval_close_et)
    eval_as_of = _eval_as_of_pct_volume(sliced_2min, desk_date)
    return (sliced_1d, sliced_2min, eval_as_of)


def session_range_json_filename(desk_date: date) -> str:
    """Filename for the desk-day JSON artifact (underscores in the date)."""
    return f'session_range_{desk_date.strftime("%Y_%m_%d")}.json'


def export_session_range_for_watchlist(
    trade_date: date | str,
    *,
    repository_dir: Path | None = None,
    save_json: bool = True,
    ib: 'IB | None' = None,
    eval_time_local: time | None = None,
) -> tuple[SessionRangeExportPayload, Path | None]:
    """Build desk session range payload for distinct watchlist symbols.

    Parameters
    ----------
    trade_date
        Desk calendar day.
    repository_dir
        Override repository day folder (for tests).
    save_json
        If True, write JSON under that folder.
    ib
        Connected IB client; required when the watchlist has symbols and data is fetched.
    eval_time_local
        If set, slice bars and evaluate sessions at this **local** wall time on ``trade_date``;
        otherwise match :func:`watchlist.tickers_on_watchlist.tickers_on_watchlist` (through ET
        session close on the desk day, ``eval_as_of`` from last 2m bar vs close).
    """
    desk_date = _parse_desk_date(trade_date)
    p_day = repository_dir if repository_dir is not None else repository_day_dir(desk_date)

    rows = tickers_on_watchlist(
        desk_date,
        repository_dir=p_day,
        save_json=False,
        fetch_atr_14=False,
        ib=None,
    )
    symbols = sorted({r.symbol for r in rows})

    tickers_out: list[SessionRangeTickerRow] = []
    eval_as_of_et_report: datetime | None = None

    if symbols and (ib is None or not ib.isConnected()):
        raise ValueError('connected ib is required when the watchlist has symbols')

    for sym in symbols:
        bundle = (
            load_bars(
                ib,
                sym,
                end_date=desk_date,
                duration_str_2min=BARS_2MIN_DURATION_FOR_DESK_SESSION_RANGES,
            )
            if ib is not None and ib.isConnected()
            else None
        )
        if bundle is None:
            tickers_out.append(
                {
                    'symbol': sym,
                    'adr_20': None,
                    'sessions': None,
                },
            )
            continue

        sliced_1d, sliced_2min, eval_as_of_et = _slice_for_eval(
            desk_date,
            bundle,
            eval_time_local,
        )
        if eval_as_of_et_report is None:
            eval_as_of_et_report = eval_as_of_et

        adr_20 = calculate_adr(
            ib,
            sym,
            bundle=BarSeries(bars_1d=sliced_1d, bars_2min=[]),
        )
        ranges = compute_desk_session_ranges(
            sliced_2min,
            session_date=desk_date,
            adr=adr_20,
            eval_as_of=eval_as_of_et,
        )
        tickers_out.append(
            {
                'symbol': sym,
                'adr_20': adr_20,
                'sessions': None if ranges is None else desk_session_ranges_to_dict(ranges),
            },
        )

    payload: SessionRangeExportPayload = {
        'trade_date': desk_date.isoformat(),
        'generated_at_pt': datetime.now(local_zone()).isoformat(),
        'eval_as_of_et': eval_as_of_et_report.isoformat(sep=' ', timespec='minutes')
        if eval_as_of_et_report is not None
        else None,
        'tickers': tickers_out,
    }

    p_written: Path | None = None
    if save_json:
        p_day.mkdir(parents=True, exist_ok=True)
        p_written = p_day / session_range_json_filename(desk_date)
        p_written.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    return (payload, p_written)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Export desk session ranges for watchlist symbols to JSON.',
    )
    parser.add_argument(
        '--date',
        metavar='YYYY-MM-DD',
        help='Desk date (default: today in local time).',
    )
    parser.add_argument(
        '--time',
        metavar='HH:MM',
        help='Optional local wall time for evaluation (default: tickers_on_watchlist semantics).',
    )
    parser.add_argument(
        '--client-id',
        type=int,
        help='Optional IB client ID override for this run.',
    )
    args = parser.parse_args()
    desk = date.fromisoformat(args.date) if args.date else date.today()
    eval_t: time | None = None
    if args.time:
        h, m = map(int, args.time.split(':', maxsplit=1))
        eval_t = time(h, m)

    client_id = args.client_id if args.client_id is not None else WATCHLIST_IB_CLIENT_ID
    ib_client = connect(readonly=True, client_id=client_id)
    try:
        payload, p_out = export_session_range_for_watchlist(
            desk,
            ib=ib_client,
            eval_time_local=eval_t,
        )
    finally:
        disconnect(ib_client)

    n = len(payload['tickers'])
    with_sessions = sum(1 for t in payload['tickers'] if t.get('sessions') is not None)
    print(
        f'date={desk.isoformat()} tickers={n} with_sessions={with_sessions} '
        f'path={p_out!s} eval_as_of_et={payload.get("eval_as_of_et")!r}',
    )


if __name__ == '__main__':
    main()
