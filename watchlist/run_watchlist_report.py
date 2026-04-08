"""Call Anthropic Claude to write the daily watchlist report (no IDE chat).

Loads ``watchlist/prompts/watchlist_report_guide.md`` plus the desk-day bundle
under ``watchlist/repository/YYYY/MM/DD/`` (newest ``gameplan_*.json`` and
``trader_tv_*.txt`` for that date, ``tickers_on_watchlist_YYYY-MM-DD.json``).
Writes ``watchlist_report_YYYY-MM-DD_HHMMSS.md`` in the same day folder.

Requires ``ANTHROPIC_API_KEY`` (e.g. in repo ``.env``). Optional ``WATCHLIST_REPORT_MODEL``
overrides the default model name.

shell cmd
uv run --frozen python -m watchlist.run_watchlist_report --date 2026-03-31
uv run --frozen python -m watchlist.run_watchlist_report --dry-run
"""

import argparse
import os
from datetime import date
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from trading.local_time import local_zone
from watchlist.sources.market_rundown import report_date_from_snapshot_file
from watchlist.sources.smb_gameplan import repository_day_dir

_WATCHLIST_PKG = Path(__file__).resolve().parent
_GUIDE_PATH = _WATCHLIST_PKG / 'prompts' / 'watchlist_report_guide.md'
_DEFAULT_MODEL = 'claude-sonnet-4-20250514'


def _newest_matching(p_day: Path, glob_pattern: str) -> Path | None:
    matches = list(p_day.glob(glob_pattern))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def _newest_market_rundown_by_report_date(p_repository: Path, desk_date: date) -> Path | None:
    """Prefer snapshots whose document date matches ``desk_date`` (any day folder)."""
    if not p_repository.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    for p_mr in p_repository.rglob('market_rundown_*.txt'):
        report_d = report_date_from_snapshot_file(p_mr)
        if report_d == desk_date:
            candidates.append((p_mr.stat().st_mtime, p_mr))
    if not candidates:
        return None
    return max(candidates)[1]


def _resolve_market_rundown_snapshot(
    p_day: Path,
    desk_date: date,
    p_repository: Path,
) -> Path | None:
    """Newest ``market_rundown_<desk>_*.txt`` in the desk folder, else repo-wide report-date match."""
    direct = _newest_matching(p_day, f'market_rundown_{desk_date.isoformat()}_*.txt')
    if direct is not None:
        return direct
    return _newest_market_rundown_by_report_date(p_repository, desk_date)


def _read_text_or_stub(label: str, p: Path | None, *, missing: str) -> str:
    if p is None or not p.is_file():
        return f'## {label}\n\n({missing})\n'
    body = p.read_text(encoding='utf-8')
    return f'## {label}\n\npath: `{p}`\n\n{body}\n'


def _build_user_prompt(
    desk_date: date,
    *,
    p_guide: Path,
    p_tickers: Path | None,
    p_gameplan: Path | None,
    p_trader_tv: Path | None,
    p_market_rundown: Path | None,
) -> str:
    parts: list[str] = [
        f'Desk date (US trading day): **{desk_date.isoformat()}**.\n',
        'Produce the watchlist report using the guide below. Cite source filenames where helpful.\n',
    ]
    parts.append(_read_text_or_stub('Guide: watchlist_report_guide.md', p_guide, missing='guide file not found'))
    parts.append(
        _read_text_or_stub(
            f'Tickers JSON: tickers_on_watchlist_{desk_date.isoformat()}.json',
            p_tickers,
            missing='tickers JSON missing — run tickers_on_watchlist after sources',
        ),
    )
    parts.append(
        _read_text_or_stub(
            'SMB gameplan snapshot (newest gameplan_YYYY-MM-DD_*.json for this desk date)',
            p_gameplan,
            missing='no gameplan snapshot for this date',
        ),
    )
    parts.append(
        _read_text_or_stub(
            'Trader TV email snapshot (newest trader_tv_YYYY-MM-DD_*.txt for this desk date)',
            p_trader_tv,
            missing='no Trader TV snapshot for this date',
        ),
    )
    parts.append(
        _read_text_or_stub(
            'Market rundown (optional: market_rundown_*.txt if present)',
            p_market_rundown,
            missing='no market rundown file for this date',
        ),
    )
    return '\n'.join(parts)


def _extract_report_text(response: object) -> str:
    out: list[str] = []
    content = getattr(response, 'content', None)
    if not isinstance(content, list):
        return ''
    for block in content:
        btype = getattr(block, 'type', None)
        if btype == 'text':
            t = getattr(block, 'text', None)
            if isinstance(t, str):
                out.append(t)
    return '\n'.join(out).strip()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description='Generate watchlist_report_*.md via Anthropic API for one desk day.',
    )
    parser.add_argument(
        '--date',
        metavar='YYYY-MM-DD',
        help='Desk date (default: today in local time, same as run_morning_sources).',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print resolved paths and exit without calling the API.',
    )
    args = parser.parse_args()

    desk_date = date.fromisoformat(args.date) if args.date else date.today()
    p_day = repository_day_dir(desk_date)
    p_repository = _WATCHLIST_PKG / 'repository'

    p_tickers = p_day / f'tickers_on_watchlist_{desk_date.isoformat()}.json'
    p_gameplan = _newest_matching(p_day, f'gameplan_{desk_date.isoformat()}_*.json')
    p_trader_tv = _newest_matching(p_day, f'trader_tv_{desk_date.isoformat()}_*.txt')
    p_mr = _resolve_market_rundown_snapshot(p_day, desk_date, p_repository)

    print(f'desk_date={desk_date.isoformat()}')
    print(f'repository_dir={p_day}')
    print(f'guide={_GUIDE_PATH}')
    print(f'tickers_json={p_tickers} exists={p_tickers.is_file()}')
    print(f'gameplan={p_gameplan}')
    print(f'trader_tv={p_trader_tv}')
    print(f'market_rundown={p_mr}')

    if args.dry_run:
        return

    if not _GUIDE_PATH.is_file():
        raise SystemExit(f'missing guide: {_GUIDE_PATH}')
    if not p_tickers.is_file():
        raise SystemExit(f'missing tickers JSON: {p_tickers}')

    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not api_key:
        raise SystemExit('ANTHROPIC_API_KEY is not set (add to .env or environment)')

    model = os.environ.get('WATCHLIST_REPORT_MODEL', _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    user_prompt = _build_user_prompt(
        desk_date,
        p_guide=_GUIDE_PATH,
        p_tickers=p_tickers if p_tickers.is_file() else None,
        p_gameplan=p_gameplan,
        p_trader_tv=p_trader_tv,
        p_market_rundown=p_mr,
    )

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=16384,
        system=(
            'You are an assistant for a US day trader. Follow the guide document in the user '
            'message exactly for structure, headings, and rules. Do not invent tickers or '
            'catalysts; use only the supplied sources. Output the full report in Markdown.'
        ),
        messages=[{'role': 'user', 'content': user_prompt}],
    )
    report_body = _extract_report_text(response)
    if not report_body:
        raise SystemExit('empty response from Anthropic')

    stamp = datetime.now(local_zone()).strftime('%H%M%S')
    p_out = p_day / f'watchlist_report_{desk_date.isoformat()}_{stamp}.md'
    p_day.mkdir(parents=True, exist_ok=True)
    header = (
        f'<!-- desk_date={desk_date.isoformat()} model={model} '
        f'generated_local={datetime.now(local_zone()).isoformat()} -->\n\n'
    )
    p_out.write_text(header + report_body + '\n', encoding='utf-8')
    print(f'wrote {p_out}')


if __name__ == '__main__':
    main()
