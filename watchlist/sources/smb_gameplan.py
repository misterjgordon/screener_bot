"""SMB morning gameplan ingestion.

Fetches the watchlist posted by SMB for a given calendar date via the
authenticated endpoint at ``rt.smbtraining.com``.

Also persists the raw payload under ``watchlist/repository/YYYY/MM/DD/`` so
multiple sources for the same desk day can share one directory and downstream
(including AI) can assemble ``what happened today`` from one place.
"""

import json
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from typing import NamedTuple
from typing import cast
from urllib.parse import urlencode

from trading.config import HTTP_BROWSER_USER_AGENT

if TYPE_CHECKING:
    import requests


class GameplanFetchOutcome(NamedTuple):
    """Result of a gameplan fetch: SMB payload, optional snapshot path, desk date used."""

    data: dict[str, object] | list[object]
    snapshot_path: Path | None
    trade_date: date


GAMEPLAN_URL = 'https://rt.smbtraining.com/api/gameplan'
GAMEPLAN_REFERER = 'https://rt.smbtraining.com/calendar'

_BROWSER_HEADERS = {
    'Origin': 'https://rt.smbtraining.com',
    'Referer': GAMEPLAN_REFERER,
    'User-Agent': HTTP_BROWSER_USER_AGENT,
}


def _parse_trade_date(trade_date: date | str) -> date:
    """Parse API/desk date input into a ``date``."""
    if isinstance(trade_date, date):
        return trade_date
    # Expects `YYYY-MM-DD` from either caller or URL query param.
    return date.fromisoformat(str(trade_date))


def _trade_date_str(trade_date: date | str) -> str:
    """Normalize to ``YYYY-MM-DD`` for the API query param."""
    return _parse_trade_date(trade_date).isoformat()


def _repository_base_dir() -> Path:
    """Return repo-relative base directory for market ingestion snapshots."""
    # watchlist/sources/smb_gameplan.py -> repo root is parents[2]
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / 'watchlist' / 'repository'


def gameplan_url_for_date(trade_date: date | str) -> str:
    """Build the gameplan request URL (useful for logging and tests)."""
    query = urlencode({'date': _trade_date_str(trade_date)})
    return f'{GAMEPLAN_URL}?{query}'


def _repository_dir_for_trade_date(trade_date: date) -> Path:
    """Return ``watchlist/repository/YYYY/MM/DD`` directory for a desk calendar day."""
    return (
        _repository_base_dir()
        / trade_date.strftime('%Y')
        / trade_date.strftime('%m')
        / trade_date.strftime('%d')
    )


def repository_day_dir(trade_date: date | str) -> Path:
    """Public path for all ingested files for one desk day (same layout as gameplan saves)."""
    return _repository_dir_for_trade_date(_parse_trade_date(trade_date))


def _snapshot_filename(trade_date: date, *, fetched_at_utc: datetime) -> str:
    """Choose a filename that won't overwrite if you refetch."""
    stamp = fetched_at_utc.strftime('%H%M%S')
    return f'gameplan_{trade_date.isoformat()}_{stamp}.json'


def _existing_gameplan_snapshot(
    trade_date: date,
    repository_dir: Path | None,
) -> Path | None:
    """Return newest on-disk gameplan snapshot for ``trade_date``, if any."""
    out_dir = repository_dir if repository_dir is not None else repository_day_dir(trade_date)
    if not out_dir.is_dir():
        return None
    matches = list(out_dir.glob(f'gameplan_{trade_date.isoformat()}_*.json'))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def save_gameplan_json(
    data: dict[str, object] | list[object],
    trade_date: date | str,
    *,
    fetched_at_utc: datetime | None = None,
    repository_dir: Path | None = None,
) -> Path:
    """Persist the raw gameplan JSON under ``repository/YYYY/MM/DD``.

    If a snapshot for the same ``trade_date`` already exists in the target
    directory (``gameplan_<date>_*.json``), returns that path and does not write
    again.

    Args:
        data: Parsed JSON payload from the SMB endpoint.
        trade_date: Desk/calendar date used for the API query param.
        fetched_at_utc: Timestamp used for unique filenames (defaults to now).
        repository_dir: Override output directory (useful for tests).

    Returns:
        Path to the saved JSON file (existing or newly written).
    """
    td = _parse_trade_date(trade_date)
    existing = _existing_gameplan_snapshot(td, repository_dir)
    if existing is not None:
        return existing

    fetched_at = fetched_at_utc or datetime.now(UTC)
    out_dir = repository_dir if repository_dir is not None else repository_day_dir(td)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _snapshot_filename(td, fetched_at_utc=fetched_at)

    payload = {
        'source': 'smb_gameplan',
        'trade_date': td.isoformat(),
        'fetched_at_utc': fetched_at.isoformat(),
        'payload': data,
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return out_path


def _fetch_gameplan_payload_root(
    session: 'requests.Session',
    trade_date: date | str,
    *,
    timeout_sec: float,
) -> dict[str, object] | list[object] | None:
    """Fetch parsed JSON root for the SMB gameplan.

    SMB sometimes returns JSON ``null`` (parsed as ``None``) for dates where no
    plan is available yet.
    """
    url = gameplan_url_for_date(trade_date)
    resp = session.get(url, headers=_BROWSER_HEADERS, timeout=timeout_sec)
    resp.raise_for_status()

    data = resp.json()
    if data is None:
        return None
    if isinstance(data, dict):
        return cast('dict[str, object]', data)
    if isinstance(data, list):
        return cast('list[object]', data)
    raise TypeError(f'Unexpected gameplan JSON root type: {type(data).__name__}')


def fetch_gameplan(
    session: 'requests.Session',
    trade_date: date | str,
    *,
    timeout_sec: float = 5.0,
    save_json: bool = True,
    repository_dir: Path | None = None,
) -> GameplanFetchOutcome:
    """GET morning gameplan for ``trade_date``.

    Args:
        session: Authenticated session from ``trading.smb_api.get_session``.
        trade_date: Calendar date the desk uses for the plan (API param ``date``).
        timeout_sec: HTTP timeout.
        save_json: If True, persist under ``watchlist/repository/YYYY/MM/DD``.
        repository_dir: Override output directory (useful for tests).

    Returns:
        Payload from SMB, optional path to the saved wrapper JSON, and the desk date used.
    """
    td = _parse_trade_date(trade_date)
    payload = _fetch_gameplan_payload_root(
        session,
        trade_date,
        timeout_sec=timeout_sec,
    )
    if payload is None:
        raise TypeError(f'SMB gameplan returned null for date={_trade_date_str(trade_date)}')

    snapshot_path: Path | None = None
    if save_json:
        snapshot_path = save_gameplan_json(payload, trade_date, repository_dir=repository_dir)

    return GameplanFetchOutcome(payload, snapshot_path, td)


def fetch_gameplan_or_none(
    session: 'requests.Session',
    trade_date: date | str,
    *,
    timeout_sec: float = 5.0,
    save_json: bool = True,
    repository_dir: Path | None = None,
) -> GameplanFetchOutcome | None:
    """Like ``fetch_gameplan`` but returns ``None`` when SMB returns JSON null."""
    td = _parse_trade_date(trade_date)
    payload = _fetch_gameplan_payload_root(
        session,
        trade_date,
        timeout_sec=timeout_sec,
    )
    if payload is None:
        return None

    snapshot_path: Path | None = None
    if save_json:
        snapshot_path = save_gameplan_json(payload, trade_date, repository_dir=repository_dir)

    return GameplanFetchOutcome(payload, snapshot_path, td)


def fetch_gameplan_today(
    session: 'requests.Session',
    *,
    start_date: date | None = None,
    timeout_sec: float = 5.0,
    save_json: bool = True,
    repository_dir: Path | None = None,
    lookback_days: int = 5,
) -> GameplanFetchOutcome:
    """Fetch gameplan with lookback from ``start_date`` (default: machine today).

    If SMB returns JSON ``null`` for the anchor date, falls back to older
    dates within ``lookback_days``.
    """
    td = start_date if start_date is not None else date.today()
    payload = _fetch_gameplan_payload_root(session, td, timeout_sec=timeout_sec)
    if payload is not None:
        snapshot_path: Path | None = None
        if save_json:
            snapshot_path = save_gameplan_json(payload, td, repository_dir=repository_dir)
        return GameplanFetchOutcome(payload, snapshot_path, td)

    for i in range(1, lookback_days + 1):
        candidate = td - timedelta(days=i)
        payload = _fetch_gameplan_payload_root(session, candidate, timeout_sec=timeout_sec)
        if payload is not None:
            snapshot_path = None
            if save_json:
                snapshot_path = save_gameplan_json(payload, candidate, repository_dir=repository_dir)
            return GameplanFetchOutcome(payload, snapshot_path, candidate)

    raise TypeError(f'SMB gameplan returned null for all dates in lookback window starting {td}')
