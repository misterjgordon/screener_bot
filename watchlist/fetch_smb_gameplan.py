"""CLI: fetch SMB morning gameplan and save under ``watchlist/repository/YYYY/MM/DD/``.

Uses ``trading.smb_api.get_session`` (same credentials as the screener).

Default: every desk date from ``2026-03-01`` through ``2026-03-25`` (inclusive).
Dates where the API returns JSON null are skipped (no error).

shell cmd
uv run --frozen python -m watchlist.fetch_smb_gameplan

Single day:

uv run --frozen python -m watchlist.fetch_smb_gameplan --date 2026-03-20

Custom inclusive range:

uv run --frozen python -m watchlist.fetch_smb_gameplan --from-date 2026-03-01 --to-date 2026-03-25

Today with lookback:

uv run --frozen python -m watchlist.fetch_smb_gameplan --today --start-date 2026-03-25
"""

import argparse
import sys
from datetime import date
from datetime import timedelta

from trading.smb_api import get_session
from watchlist.sources.smb_gameplan import fetch_gameplan_or_none
from watchlist.sources.smb_gameplan import fetch_gameplan_today

_DEFAULT_FROM = date(2026, 3, 1)
_DEFAULT_TO = date(2026, 3, 25)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Fetch SMB /api/gameplan; write wrapper JSON under watchlist/repository/YYYY/MM/DD.',
    )
    parser.add_argument(
        '--date',
        metavar='YYYY-MM-DD',
        help='Single desk date (skips if API returns null).',
    )
    parser.add_argument(
        '--from-date',
        metavar='YYYY-MM-DD',
        default=_DEFAULT_FROM.isoformat(),
        help=f'Range start inclusive (default: {_DEFAULT_FROM.isoformat()}).',
    )
    parser.add_argument(
        '--to-date',
        metavar='YYYY-MM-DD',
        default=_DEFAULT_TO.isoformat(),
        help=f'Range end inclusive (default: {_DEFAULT_TO.isoformat()}).',
    )
    parser.add_argument(
        '--today',
        action='store_true',
        help='Fetch using today/lookback logic instead of a date range.',
    )
    parser.add_argument(
        '--start-date',
        metavar='YYYY-MM-DD',
        help='Anchor for lookback when using --today (default: machine today).',
    )
    parser.add_argument(
        '--lookback-days',
        type=int,
        default=14,
        help='Days to walk back when using --today (default: 14).',
    )
    args = parser.parse_args()

    if args.date and args.today:
        parser.error('Use either --date or --today, not both.')

    session = get_session()

    if args.today:
        start = date.fromisoformat(args.start_date) if args.start_date else None
        outcome = fetch_gameplan_today(
            session,
            start_date=start,
            save_json=True,
            lookback_days=args.lookback_days,
        )
        print(outcome.snapshot_path)
        print(f'resolved_trade_date={outcome.trade_date.isoformat()}')
        return

    if args.date:
        outcome = fetch_gameplan_or_none(session, args.date, save_json=True)
        if outcome is None:
            print(f'skip {args.date} (no gameplan)', file=sys.stderr)
            return
        print(outcome.snapshot_path)
        print(f'resolved_trade_date={outcome.trade_date.isoformat()}')
        return

    d0 = date.fromisoformat(args.from_date)
    d1 = date.fromisoformat(args.to_date)
    if d1 < d0:
        parser.error('--to-date must be on or after --from-date')

    saved = 0
    skipped = 0
    d = d0
    while d <= d1:
        outcome = fetch_gameplan_or_none(session, d, save_json=True)
        if outcome is None:
            print(f'skip {d.isoformat()} (no gameplan)')
            skipped += 1
        else:
            print(f'ok {d.isoformat()} {outcome.snapshot_path}')
            saved += 1
        d += timedelta(days=1)

    print(f'summary saved={saved} skipped={skipped}')


if __name__ == '__main__':
    main()
