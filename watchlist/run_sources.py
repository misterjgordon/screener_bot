"""Run all configured watchlist source ingestors for one desk day.

Market rundown is fetched only when ``trade_date`` is a **Thursday** (see
``--force-market-rundown`` to override).

shell cmd
uv run --frozen python -m watchlist.run_sources --date 2026-04-08

if --gmail-interactive-auth is passed, it will open the Google OAuth browser flow to re-authorize the Gmail token
uv run --frozen python -m watchlist.run_sources --date 2026-04-08 --gmail-interactive-auth
"""

import argparse
import functools
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING
from typing import cast

from trading.integrations.google import get_gmail_api
from trading.market_data import connect
from trading.market_data import disconnect
from trading.smb_api import get_session
from watchlist.sources.market_rundown import fetch_market_rundown
from watchlist.sources.smb_gameplan import fetch_gameplan_or_none
from watchlist.sources.tradertv import GmailApi
from watchlist.sources.tradertv import fetch_tradertv_watchlist_email_or_none
from watchlist.tickers_on_watchlist import tickers_on_watchlist

if TYPE_CHECKING:
    import requests


def _run_smb_gameplan(session: object, trade_date: date) -> tuple[str, str]:
    """Run SMB gameplan source and return status + message."""
    # Runtime session type is requests.Session from trading.smb_api.
    outcome = fetch_gameplan_or_none(session, trade_date, save_json=True)  # type: ignore[arg-type]
    if outcome is None:
        return 'skip', f'smb_gameplan date={trade_date.isoformat()} no data'
    return 'ok', f'smb_gameplan date={trade_date.isoformat()} path={outcome.snapshot_path}'


def _run_market_rundown(
    session: 'requests.Session',
    trade_date: date,
    *,
    force: bool = False,
) -> tuple[str, str]:
    """Run market rundown Google Doc export and return status + message.

    The export is intended for **Thursday** publishes only; other days are
    skipped unless ``force`` is True.
    """
    # Thursday == 3 (Monday=0).
    if not force and trade_date.weekday() != 3:
        return (
            'skip',
            f'market_rundown date={trade_date.isoformat()} skipped (doc updates Thursdays only;'
            f' weekday={trade_date.weekday()}; use --force-market-rundown to fetch anyway)',
        )
    strict_header_date = not force
    try:
        outcome = fetch_market_rundown(
            session,
            trade_date,
            save_text=True,
            require_matching_trade_date=strict_header_date,
        )
    except ValueError as exc:
        if strict_header_date and trade_date.weekday() == 3:
            try:
                outcome = fetch_market_rundown(
                    session,
                    trade_date,
                    save_text=True,
                    require_matching_trade_date=False,
                )
            except ValueError as exc2:
                return (
                    'skip',
                    f'market_rundown date={trade_date.isoformat()} skipped ({exc2})',
                )
            return (
                'ok',
                f'market_rundown date={trade_date.isoformat()} report_date='
                f'{outcome.report_date.isoformat() if outcome.report_date is not None else "unknown"} '
                f'path={outcome.snapshot_path} (saved though doc header date differed: {exc})',
            )
        return (
            'skip',
            f'market_rundown date={trade_date.isoformat()} skipped ({exc})',
        )
    return (
        'ok',
        f'market_rundown date={trade_date.isoformat()} report_date='
        f'{outcome.report_date.isoformat() if outcome.report_date is not None else "unknown"} '
        f'path={outcome.snapshot_path}',
    )


def _run_tradertv_watchlist(
    gmail_api: GmailApi,
    trade_date: date,
) -> tuple[str, str]:
    """Run TraderTV watchlist Gmail source and return status + message."""
    outcome = fetch_tradertv_watchlist_email_or_none(
        gmail_api,
        trade_date,
        save_text=True,
    )
    if outcome is None:
        return 'skip', f'tradertv_watchlist date={trade_date.isoformat()} no email'
    return (
        'ok',
        f'tradertv_watchlist date={trade_date.isoformat()}'
        f' subject={outcome.subject!r} path={outcome.snapshot_path}',
    )


def main() -> None:
    parser = argparse.ArgumentParser(description='Run all watchlist sources for one desk day.')
    parser.add_argument(
        '--date',
        metavar='YYYY-MM-DD',
        help='Desk date to ingest (default: machine local date).',
    )
    parser.add_argument(
        '--force-market-rundown',
        action='store_true',
        help='Fetch market rundown even when desk date is not Thursday.',
    )
    parser.add_argument(
        '--gmail-interactive-auth',
        action='store_true',
        help='Open Google OAuth browser flow to re-authorize Gmail token.',
    )
    args = parser.parse_args()

    trade_date = date.fromisoformat(args.date) if args.date else date.today()
    session = get_session()
    gmail_api = cast(
        'GmailApi',
        get_gmail_api(interactive=args.gmail_interactive_auth),
    )

    run_smb = functools.partial(_run_smb_gameplan, session, trade_date)
    run_mr = functools.partial(
        _run_market_rundown,
        session,
        trade_date,
        force=args.force_market_rundown,
    )
    run_tradertv = functools.partial(_run_tradertv_watchlist, gmail_api, trade_date)
    steps: list[tuple[str, Callable[[], tuple[str, str]]]] = [
        ('smb_gameplan', run_smb),
        ('market_rundown', run_mr),
        ('tradertv_watchlist', run_tradertv),
    ]
    ok = 0
    skipped = 0
    failed = 0

    for step_name, runner in steps:
        try:
            status, msg = runner()
        except Exception as exc:
            failed += 1
            print(f'error {step_name} {exc}')
            continue

        print(msg)
        if status == 'ok':
            ok += 1
        elif status == 'skip':
            skipped += 1
        else:
            failed += 1

    print(f'summary ok={ok} skipped={skipped} failed={failed}')
    if failed > 0:
        raise SystemExit(1)

    ib = connect(readonly=True)
    try:
        wl_rows = tickers_on_watchlist(trade_date, ib=ib, save_json=True)
        with_atr = sum(1 for r in wl_rows if r.atr_14 is not None)
        print(
            f'tickers_on_watchlist date={trade_date.isoformat()} count={len(wl_rows)}'
            f' with_atr_14={with_atr}'
        )
    finally:
        disconnect(ib)


if __name__ == '__main__':
    main()
