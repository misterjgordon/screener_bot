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

from bs4 import BeautifulSoup

from watchlist.sources.smb_gameplan import repository_day_dir

TRADERTV_FROM_EMAIL = 'tradertv-live@mail.beehiiv.com'
# Beehiiv switched subject prefix around 2026-08-04 (dropped the space in "Trader TV").
TRADERTV_SUBJECT_PREFIX = 'TraderTV Watchlist - '
TRADERTV_SUBJECT_PREFIX_LEGACY = 'Trader TV Watchlist - '
_TRAILING_VIEW_IMAGE_MARKER = '----------View image:'
_IMAGE_LINE_PREFIXES = (
    'View image:',
    'Follow image link:',
    'Caption:',
)
# Beehiiv now ships a stub text/plain part that only links to the web post; real
# content is in text/html. Treat short "view online" bodies as empty.
_PLAIN_TEXT_STUB_MARKER = 'You are reading a plain text version of this post'
_MIN_USEFUL_BODY_CHARS = 400


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


def _human_date_for_subject(trade_date: date) -> str:
    """Desk date as Beehiiv formats it in the watchlist subject (e.g. ``August 5, 2026``)."""
    return f'{trade_date.strftime("%B")} {trade_date.day}, {trade_date.year}'


def _subject_for_trade_date(trade_date: date) -> str:
    """Current Beehiiv subject line for ``trade_date``."""
    return f'{TRADERTV_SUBJECT_PREFIX}{_human_date_for_subject(trade_date)}'


def _subjects_for_trade_date(trade_date: date) -> tuple[str, str]:
    """Current and legacy subject lines (Beehiiv renamed the prefix)."""
    human_date = _human_date_for_subject(trade_date)
    return (
        f'{TRADERTV_SUBJECT_PREFIX}{human_date}',
        f'{TRADERTV_SUBJECT_PREFIX_LEGACY}{human_date}',
    )


def _gmail_query_for_trade_date(trade_date: date) -> str:
    """Gmail search matching current or legacy TraderTV watchlist subject for the desk day."""
    current, legacy = _subjects_for_trade_date(trade_date)
    subject_clause = f'(subject:("{current}") OR subject:("{legacy}"))'
    return f'from:({TRADERTV_FROM_EMAIL}) {subject_clause}'


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
        existing_text = existing.read_text(encoding='utf-8')
        # Ignore snapshot header comments when deciding if the body is a stub.
        existing_body_lines = [
            line for line in existing_text.splitlines() if not line.startswith('#')
        ]
        existing_body = '\n'.join(existing_body_lines).strip()
        if not _is_plain_text_stub(existing_body):
            return existing

    fetched_at = fetched_at_utc or datetime.now(UTC)
    out_dir = repository_dir if repository_dir is not None else repository_day_dir(trade_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = existing if existing is not None else out_dir / _snapshot_filename(
        trade_date,
        fetched_at_utc=fetched_at,
    )
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


def _iter_mime_parts(payload: dict[str, object]) -> list[dict[str, object]]:
    """Flatten multipart payloads (top-level + one nesting level)."""
    out: list[dict[str, object]] = [payload]
    parts = payload.get('parts')
    if not isinstance(parts, list):
        return out
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_map = cast('dict[str, object]', part)
        out.append(part_map)
        nested = part_map.get('parts')
        if not isinstance(nested, list):
            continue
        for nested_part in nested:
            if isinstance(nested_part, dict):
                out.append(cast('dict[str, object]', nested_part))
    return out


def _decode_part_body(part: dict[str, object]) -> str | None:
    body = part.get('body')
    if not isinstance(body, dict):
        return None
    encoded = cast('dict[str, object]', body).get('data')
    if not isinstance(encoded, str) or not encoded:
        return None
    return _decode_gmail_base64(encoded).strip()


def _extract_mime_text(message: dict[str, object], mime_type: str) -> str:
    """Return the first decoded body for ``mime_type``, or empty string."""
    payload = message.get('payload')
    if not isinstance(payload, dict):
        return ''
    for part in _iter_mime_parts(cast('dict[str, object]', payload)):
        if part.get('mimeType') != mime_type:
            continue
        decoded = _decode_part_body(part)
        if decoded:
            return decoded
    return ''


def _html_to_plain_text(html: str) -> str:
    """Convert Beehiiv HTML watchlist body to newline-separated plain text."""
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text('\n', strip=True).strip()


def _is_plain_text_stub(text: str) -> bool:
    """True when text/plain is only Beehiiv's \"view online\" placeholder."""
    stripped = text.strip()
    if not stripped:
        return True
    if _PLAIN_TEXT_STUB_MARKER in stripped:
        return True
    return 'beehiiv.com/p/' in stripped and len(stripped) < _MIN_USEFUL_BODY_CHARS


def _extract_text_plain(message: dict[str, object]) -> str:
    """Prefer useful text/plain; fall back to HTML when Beehiiv sends a stub."""
    plain = _extract_mime_text(message, 'text/plain')
    if plain and not _is_plain_text_stub(plain):
        return plain

    html = _extract_mime_text(message, 'text/html')
    if html:
        return _html_to_plain_text(html)

    if plain:
        return plain
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
