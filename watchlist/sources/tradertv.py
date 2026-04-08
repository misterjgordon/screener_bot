"""TraderTV watchlist ingestion from Gmail.

Reads the TraderTV daily watchlist email using the Gmail API and writes a
snapshot under ``watchlist/repository/YYYY/MM/DD``.
"""

import base64
from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import NamedTuple
from typing import Protocol
from typing import cast

from watchlist.sources.smb_gameplan import repository_day_dir

TRADERTV_FROM_EMAIL = 'tradertv-live@mail.beehiiv.com'
TRADERTV_SUBJECT_PREFIX = 'Trader TV Watchlist - '
_TRAILING_VIEW_IMAGE_MARKER = '----------View image:'
_IMAGE_LINE_PREFIXES = (
    'View image:',
    'Follow image link:',
    'Caption:',
)


class GmailApi(Protocol):
    def users(self) -> 'GmailUsersApi':
        ...


class GmailUsersApi(Protocol):
    def messages(self) -> 'GmailMessagesApi':
        ...


class GmailMessagesApi(Protocol):
    def list(self, **kw: object) -> 'GmailRequest':
        ...

    def get(self, **kw: object) -> 'GmailRequest':
        ...


class GmailRequest(Protocol):
    def execute(self) -> dict[str, object]:
        ...


class TraderTvWatchlistOutcome(NamedTuple):
    """Parsed email body plus optional snapshot path for a desk day."""

    message_id: str
    subject: str
    from_email: str
    date_header: str
    body_text: str
    snapshot_path: Path | None
    trade_date: date


def _subject_for_trade_date(trade_date: date) -> str:
    human_date = f'{trade_date.strftime("%B")} {trade_date.day}, {trade_date.year}'
    return f'{TRADERTV_SUBJECT_PREFIX}{human_date}'


def _gmail_query_for_trade_date(trade_date: date) -> str:
    subject = _subject_for_trade_date(trade_date)
    return f'from:({TRADERTV_FROM_EMAIL}) subject:("{subject}")'


def _snapshot_filename(trade_date: date, *, fetched_at_utc: datetime) -> str:
    stamp = fetched_at_utc.strftime('%H%M%S')
    return f'trader_tv_{trade_date.isoformat()}_{stamp}.txt'


def _existing_tradertv_snapshot(
    trade_date: date,
    repository_dir: Path | None,
) -> Path | None:
    out_dir = repository_dir if repository_dir is not None else repository_day_dir(trade_date)
    if not out_dir.is_dir():
        return None
    matches = list(out_dir.glob(f'trader_tv_{trade_date.isoformat()}_*.txt'))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def save_tradertv_watchlist_text(
    body: str,
    trade_date: date,
    *,
    subject: str,
    from_email: str,
    date_header: str,
    fetched_at_utc: datetime | None = None,
    repository_dir: Path | None = None,
) -> Path:
    """Write TraderTV watchlist text under ``watchlist/repository/YYYY/MM/DD``."""
    existing = _existing_tradertv_snapshot(trade_date, repository_dir)
    if existing is not None:
        return existing

    fetched_at = fetched_at_utc or datetime.now(UTC)
    out_dir = repository_dir if repository_dir is not None else repository_day_dir(trade_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _snapshot_filename(trade_date, fetched_at_utc=fetched_at)
    header = (
        f'# source=tradertv_watchlist desk_date={trade_date.isoformat()}'
        f' fetched_at_utc={fetched_at.isoformat()}\n'
        f'# subject={subject}\n'
        f'# from={from_email}\n'
        f'# date_header={date_header}\n\n'
    )
    out_path.write_text(header + body.strip(), encoding='utf-8')
    return out_path


def _header_map(message: dict[str, object]) -> dict[str, str]:
    payload = message.get('payload')
    if not isinstance(payload, dict):
        return {}
    payload_map = cast('dict[str, object]', payload)
    headers = payload_map.get('headers')
    if not isinstance(headers, list):
        return {}

    out: dict[str, str] = {}
    for h in headers:
        if not isinstance(h, dict):
            continue
        header = cast('dict[str, object]', h)
        name = header.get('name')
        value = header.get('value')
        if isinstance(name, str) and isinstance(value, str):
            out[name.lower()] = value
    return out


def _decode_gmail_base64(data: str) -> str:
    padded = data + ('=' * ((4 - len(data) % 4) % 4))
    decoded = base64.urlsafe_b64decode(padded.encode('ascii'))
    return decoded.decode('utf-8', errors='replace')


def _extract_text_plain(message: dict[str, object]) -> str:
    payload = message.get('payload')
    if not isinstance(payload, dict):
        snippet = message.get('snippet')
        return snippet if isinstance(snippet, str) else ''
    payload_map = cast('dict[str, object]', payload)

    body = payload_map.get('body')
    if isinstance(body, dict):
        body_map = cast('dict[str, object]', body)
        encoded = body_map.get('data')
        if isinstance(encoded, str) and encoded:
            return _decode_gmail_base64(encoded).strip()

    parts = payload_map.get('parts')
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_map = cast('dict[str, object]', part)
            if part_map.get('mimeType') != 'text/plain':
                continue
            part_body = part_map.get('body')
            if not isinstance(part_body, dict):
                continue
            part_body_map = cast('dict[str, object]', part_body)
            encoded = part_body_map.get('data')
            if isinstance(encoded, str) and encoded:
                return _decode_gmail_base64(encoded).strip()

    snippet = message.get('snippet')
    return snippet.strip() if isinstance(snippet, str) else ''


def _trim_trailing_view_image_block(body: str) -> str:
    """Remove trailing Beehiiv promo/footer block that starts at ``View image:``."""
    idx_news = body.find('# **In The News')
    idx_marker = body.rfind(_TRAILING_VIEW_IMAGE_MARKER)
    if idx_marker != -1 and (idx_news == -1 or idx_marker > idx_news):
        return body[:idx_marker].rstrip()
    return body.strip()


def _strip_image_lines(body: str) -> str:
    """Drop image-only lines emitted by Beehiiv plain-text exports."""
    lines = body.splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(_IMAGE_LINE_PREFIXES):
            continue
        kept.append(line)
    return '\n'.join(kept).strip()


def fetch_tradertv_watchlist_email_or_none(
    gmail_api: GmailApi,
    trade_date: date,
    *,
    save_text: bool = True,
    repository_dir: Path | None = None,
) -> TraderTvWatchlistOutcome | None:
    """Fetch TraderTV watchlist email for ``trade_date`` and optionally save."""
    query = _gmail_query_for_trade_date(trade_date)
    list_result = gmail_api.users().messages().list(userId='me', q=query, maxResults=5).execute()
    messages = list_result.get('messages') if isinstance(list_result, dict) else None
    if not isinstance(messages, list) or not messages:
        return None

    first = messages[0]
    if not isinstance(first, dict) or 'id' not in first:
        return None
    message_id = first.get('id')
    if not isinstance(message_id, str) or not message_id:
        return None

    message = gmail_api.users().messages().get(userId='me', id=message_id, format='full').execute()
    if not isinstance(message, dict):
        return None

    headers = _header_map(message)
    subject = headers.get('subject', '')
    from_email = headers.get('from', '')
    date_header = headers.get('date', '')
    body_text = _extract_text_plain(message)
    body_text = _strip_image_lines(body_text)
    body_text = _trim_trailing_view_image_block(body_text)
    if not body_text:
        raise ValueError(f'TraderTV email had empty text body for date={trade_date.isoformat()}')

    snapshot_path: Path | None = None
    if save_text:
        snapshot_path = save_tradertv_watchlist_text(
            body_text,
            trade_date,
            subject=subject,
            from_email=from_email,
            date_header=date_header,
            repository_dir=repository_dir,
        )

    return TraderTvWatchlistOutcome(
        message_id=message_id,
        subject=subject,
        from_email=from_email,
        date_header=date_header,
        body_text=body_text,
        snapshot_path=snapshot_path,
        trade_date=trade_date,
    )
