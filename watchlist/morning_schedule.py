"""Gate scheduled morning ingest: poll during a local window, one catch-up after.

LaunchAgent fires every two minutes while SMB gameplan may still be publishing.
After the last poll slot, only the first invocation runs (atomic marker); later
slots and duplicate agents skip. Manual runs use ``WATCHLIST_MORNING_FORCE=1`` or
``make watchlist-run-now`` (``run_sources`` directly).
"""

import argparse
import sys
from datetime import date
from datetime import datetime
from datetime import time
from pathlib import Path

from trading.local_time import local_zone
from watchlist.sources.smb_gameplan import repository_day_dir

# Local wall-clock poll window (gameplan often lands ~5:58–6:10 PT).
POLL_FIRST_LOCAL = time(5, 58)
POLL_LAST_LOCAL = time(6, 10)
LATE_CATCHUP_MARKER = '.watchlist_morning_after_poll_done'


def _as_local(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(local_zone())
    if now.tzinfo is None:
        return now.replace(tzinfo=local_zone())
    return now.astimezone(local_zone())


def in_polling_window(now: datetime) -> bool:
    """True when ``now`` (local) is inside the inclusive morning poll window."""
    t = _as_local(now).time()
    return POLL_FIRST_LOCAL <= t <= POLL_LAST_LOCAL


def before_polling_window(now: datetime) -> bool:
    """True when ``now`` (local) is earlier than the first poll slot."""
    return _as_local(now).time() < POLL_FIRST_LOCAL


def late_catchup_marker_path(
    desk_date: date,
    repository_dir: Path | None = None,
) -> Path:
    """Path to the after-window single-run marker for one desk day."""
    day_dir = repository_dir if repository_dir is not None else repository_day_dir(desk_date)
    return day_dir / LATE_CATCHUP_MARKER


def try_acquire_late_catchup(
    desk_date: date,
    *,
    repository_dir: Path | None = None,
    acquired_at: datetime | None = None,
) -> bool:
    """Claim the one allowed run after the poll window; False if already claimed."""
    marker = late_catchup_marker_path(desk_date, repository_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    stamp = _as_local(acquired_at).isoformat()
    try:
        with marker.open('x', encoding='utf-8') as fh:
            fh.write(f'acquired_at_local={stamp}\n')
    except FileExistsError:
        return False
    return True


def should_run_scheduled_gate(
    desk_date: date,
    *,
    now: datetime | None = None,
    repository_dir: Path | None = None,
) -> tuple[bool, str]:
    """Whether ``run_morning_sources`` should run for this launchd invocation."""
    local_now = _as_local(now)
    if in_polling_window(local_now):
        return True, 'in_poll_window'
    if before_polling_window(local_now):
        return False, 'before_poll_window'
    if try_acquire_late_catchup(desk_date, repository_dir=repository_dir, acquired_at=local_now):
        return True, 'late_catchup_once'
    return False, 'late_catchup_already_done'


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Gate scheduled morning ingest (poll window vs one late catch-up).',
    )
    parser.add_argument(
        '--date',
        metavar='YYYY-MM-DD',
        help='Desk date (default: today in local time).',
    )
    args = parser.parse_args()
    desk_date = date.fromisoformat(args.date) if args.date else datetime.now(local_zone()).date()
    run, reason = should_run_scheduled_gate(desk_date)
    if run:
        print(f'run desk_date={desk_date.isoformat()} reason={reason}')
        return
    print(f'skip desk_date={desk_date.isoformat()} reason={reason}', file=sys.stderr)
    raise SystemExit(1)


if __name__ == '__main__':
    main()
