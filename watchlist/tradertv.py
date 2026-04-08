"""CLI for TraderTV watchlist Gmail source.

shell cmd
uv run --frozen python -m watchlist.tradertv --date 2026-03-20
"""

import argparse
from datetime import date
from typing import cast

from trading.integrations.google import get_gmail_api
from watchlist.sources.tradertv import GmailApi
from watchlist.sources.tradertv import fetch_tradertv_watchlist_email_or_none


def main() -> None:
    parser = argparse.ArgumentParser(description='Fetch TraderTV watchlist email for one desk day.')
    parser.add_argument(
        '--date',
        metavar='YYYY-MM-DD',
        help='Desk date to fetch (default: machine local date).',
    )
    args = parser.parse_args()

    trade_date = date.fromisoformat(args.date) if args.date else date.today()
    gmail_api = cast('GmailApi', get_gmail_api(interactive=False))
    outcome = fetch_tradertv_watchlist_email_or_none(
        gmail_api,
        trade_date,
        save_text=True,
    )
    if outcome is None:
        print(f'tradertv_watchlist date={trade_date.isoformat()} no email')
        raise SystemExit(1)

    print(
        f'tradertv_watchlist date={trade_date.isoformat()}'
        f' subject={outcome.subject!r} path={outcome.snapshot_path}'
    )


if __name__ == '__main__':
    main()
