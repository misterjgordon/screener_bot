"""Market rundown from the SMBU Google Doc (plain text export).

The doc is updated on **Thursdays** only. :mod:`watchlist.run_sources` therefore
fetches it when ``trade_date`` is a Thursday (local calendar), so other weekdays
do not re-save the same stale body under a new ``desk_date`` path. The first
content line is the rundown’s publication date and often **does not** equal the
desk day; on Thursday, ingestion retries without strict date matching so the
snapshot is still saved under the desk folder. Use ``--force-market-rundown`` to
fetch on other weekdays.
"""

from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import NamedTuple

if TYPE_CHECKING:
    import requests

from trading.config import HTTP_BROWSER_USER_AGENT
from watchlist.sources.smb_gameplan import repository_day_dir

MARKET_RUNDOWN_EXPORT_URL = (
    'https://docs.google.com/document/d/'
    '1OYbEMcnTxgcHPBNqJxMZ73adqNBFC5KvoHVytNBmD_o/export?format=txt'
)

_BROWSER_HEADERS = {'User-Agent': HTTP_BROWSER_USER_AGENT}


class MarketRundownOutcome(NamedTuple):
    """Plain text body, optional snapshot path, desk date used for the file path."""

    text: str
    snapshot_path: Path | None
    trade_date: date
    report_date: date | None


def _extract_report_date(text: str) -> date | None:
    """Parse the first non-empty line as ``Mon DD, YYYY`` and return the date."""
    first_non_empty_line = next((line.strip() for line in text.splitlines() if line.strip()), '')
    if not first_non_empty_line:
        return None
    cleaned_line = first_non_empty_line.lstrip('\ufeff')
    try:
        return datetime.strptime(cleaned_line, '%b %d, %Y').date()
    except ValueError:
        return None


def report_date_from_snapshot_file(p_path: Path) -> date | None:
    """Parse the rundown document date from a saved snapshot.

    Strips leading ``#`` ingest header lines, then parses the first body line as
    ``%b %d, %Y`` (same rule as the live Google export).
    """
    text = p_path.read_text(encoding='utf-8')
    body_lines: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith('#'):
            continue
        body_lines.append(line)
    body = '\n'.join(body_lines)
    return _extract_report_date(body)


def _snapshot_filename(trade_date: date, *, fetched_at_utc: datetime) -> str:
    stamp = fetched_at_utc.strftime('%H%M%S')
    return f'market_rundown_{trade_date.isoformat()}_{stamp}.txt'


def _existing_market_rundown_snapshot(
    trade_date: date,
    repository_dir: Path | None,
) -> Path | None:
    """Return newest on-disk market rundown snapshot for ``trade_date``, if any."""
    out_dir = repository_dir if repository_dir is not None else repository_day_dir(trade_date)
    if not out_dir.is_dir():
        return None
    matches = list(out_dir.glob(f'market_rundown_{trade_date.isoformat()}_*.txt'))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def save_market_rundown_text(
    body: str,
    trade_date: date,
    *,
    fetched_at_utc: datetime | None = None,
    repository_dir: Path | None = None,
) -> Path:
    """Write text under ``watchlist/repository/YYYY/MM/DD`` with a short header.

    If a snapshot for the same ``trade_date`` already exists in the target
    directory (``market_rundown_<date>_*.txt``), returns that path and does not
    write again.
    """
    existing = _existing_market_rundown_snapshot(trade_date, repository_dir)
    if existing is not None:
        return existing

    fetched_at = fetched_at_utc or datetime.now(UTC)
    out_dir = repository_dir if repository_dir is not None else repository_day_dir(trade_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _snapshot_filename(trade_date, fetched_at_utc=fetched_at)
    header = f'# desk_date={trade_date.isoformat()} fetched_at_utc={fetched_at.isoformat()}\n\n'
    out_path.write_text(header + body, encoding='utf-8')
    return out_path


def fetch_market_rundown(
    session: 'requests.Session',
    trade_date: date,
    *,
    timeout_sec: float = 30.0,
    save_text: bool = True,
    require_matching_trade_date: bool = True,
    repository_dir: Path | None = None,
) -> MarketRundownOutcome:
    """GET the doc as UTF-8 text via Google's export endpoint and optionally save."""
    resp = session.get(
        MARKET_RUNDOWN_EXPORT_URL,
        headers=_BROWSER_HEADERS,
        timeout=timeout_sec,
    )
    resp.raise_for_status()
    text = resp.text.strip()
    if not text:
        raise ValueError('Market rundown export returned an empty body')
    report_date = _extract_report_date(text)
    if require_matching_trade_date and report_date != trade_date:
        raise ValueError(
            'Market rundown date does not match requested trade date: '
            f'report_date={report_date.isoformat() if report_date is not None else "unknown"} '
            f'trade_date={trade_date.isoformat()}'
        )

    snapshot_path: Path | None = None
    if save_text:
        snapshot_path = save_market_rundown_text(
            text,
            trade_date,
            repository_dir=repository_dir,
        )

    return MarketRundownOutcome(text, snapshot_path, trade_date, report_date)
