"""Briefing.com *Page One* macro summary (HTML fragment).

The marketing URL ``/page-one`` is a client-rendered shell; the server-rendered
body is fetched from ``/Inv/content/PageOne/default.htm``, which carries the
same article text the SPA loads.

Ingestion is normally run via ``watchlist.run_sources`` (``briefing_page_one`` step).
To fetch **only** this source into the repository:

shell cmd
uv run --frozen python -m watchlist.sources.briefing_page_one --date 2026-04-09
"""

import argparse
from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import requests
from bs4 import BeautifulSoup

from trading.config import HTTP_BROWSER_USER_AGENT
from watchlist.sources.smb_gameplan import repository_day_dir

BRIEFING_PAGE_ONE_HTML_URL = 'https://www.briefing.com/Inv/content/PageOne/default.htm'

_BROWSER_HEADERS = {'User-Agent': HTTP_BROWSER_USER_AGENT}


class BriefingPageOneOutcome(NamedTuple):
    """Plain text body plus optional snapshot path."""

    text: str
    snapshot_path: Path | None
    trade_date: date
    source_url: str


def _snapshot_filename(trade_date: date, *, fetched_at_utc: datetime) -> str:
    stamp = fetched_at_utc.strftime('%H%M%S')
    return f'briefing_page_one_{trade_date.isoformat()}_{stamp}.txt'


def extract_briefing_page_one_plain_text(html: str) -> str:
    """Parse Page One HTML and return a readable plain-text body.

    Raises ``ValueError`` when structure is missing or content is implausibly short.
    """
    soup = BeautifulSoup(html, 'html.parser')
    content_root = soup.select_one('#Content')
    if content_root is None:
        raise ValueError('Briefing Page One HTML missing #Content')

    time_el = content_root.select_one('.colTime')
    title_el = content_root.select_one('.colTitle')
    article_el = content_root.select_one('.colArticle')

    parts: list[str] = []
    if time_el is not None:
        parts.append(time_el.get_text(' ', strip=True))
    if title_el is not None:
        t = title_el.get_text(strip=True)
        if t:
            parts.extend(['', t, ''])
    if article_el is not None:
        parts.append(article_el.get_text('\n', strip=True))

    text = '\n'.join(parts).strip()
    if len(text) < 200:
        raise ValueError(
            'Briefing Page One extract too short; page structure may have changed or access denied',
        )
    return text


def save_briefing_page_one_text(
    body: str,
    trade_date: date,
    *,
    source_url: str,
    fetched_at_utc: datetime | None = None,
    repository_dir: Path | None = None,
) -> Path:
    """Write plain text under ``watchlist/repository/YYYY/MM/DD``."""
    fetched_at = fetched_at_utc or datetime.now(UTC)
    out_dir = repository_dir if repository_dir is not None else repository_day_dir(trade_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _snapshot_filename(trade_date, fetched_at_utc=fetched_at)
    header = (
        f'# source=briefing_page_one desk_date={trade_date.isoformat()}'
        f' fetched_at_utc={fetched_at.isoformat()}\n'
        f'# url={source_url}\n\n'
    )
    out_path.write_text(header + body.strip() + '\n', encoding='utf-8')
    return out_path


def fetch_briefing_page_one(
    session: requests.Session,
    trade_date: date,
    *,
    timeout_sec: float = 30.0,
    save_text: bool = True,
    html_url: str = BRIEFING_PAGE_ONE_HTML_URL,
    repository_dir: Path | None = None,
) -> BriefingPageOneOutcome:
    """GET Page One HTML, extract plain text, and optionally save."""
    resp = session.get(html_url, headers=_BROWSER_HEADERS, timeout=timeout_sec)
    resp.raise_for_status()
    text = extract_briefing_page_one_plain_text(resp.text)
    snapshot_path: Path | None = None
    if save_text:
        snapshot_path = save_briefing_page_one_text(
            text,
            trade_date,
            source_url=html_url,
            repository_dir=repository_dir,
        )
    return BriefingPageOneOutcome(text, snapshot_path, trade_date, html_url)


def main() -> None:
    """CLI: GET Page One and save ``briefing_page_one_*.txt`` under the desk-day folder."""
    parser = argparse.ArgumentParser(description='Fetch Briefing.com Page One into watchlist/repository.')
    parser.add_argument(
        '--date',
        metavar='YYYY-MM-DD',
        help='Desk date folder (default: today, local).',
    )
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.date) if args.date else date.today()
    session = requests.Session()
    outcome = fetch_briefing_page_one(session, trade_date, save_text=True)
    print(f'desk_date={trade_date.isoformat()}')
    print(f'path={outcome.snapshot_path}')
    print(f'chars={len(outcome.text)}')


if __name__ == '__main__':
    main()
