"""Call Anthropic Claude to write the daily watchlist report (no IDE chat).

Loads ``watchlist/prompts/watchlist_report_guide.md`` plus the desk-day bundle
under ``watchlist/repository/YYYY/MM/DD/`` (newest ``gameplan_*.json``,
``trader_tv_*.txt``, ``briefing_page_one_*.txt`` for that date,
``tickers_on_watchlist_YYYY-MM-DD.json``, optional ``market_rundown_*.txt``).
Writes ``watchlist_report_YYYY-MM-DD_HHMMSS.md`` in the same day folder.

Requires ``ANTHROPIC_API_KEY`` (e.g. in repo ``.env``). Optional ``WATCHLIST_REPORT_MODEL``
overrides the default model name.

shell cmd
uv run --frozen python -m watchlist.run_ai_watchlist --date 2026-04-30
uv run --frozen python -m watchlist.run_ai_watchlist
"""

import argparse
import json
import os
import re
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import cast

from anthropic import Anthropic
from anthropic.types import Message
from anthropic.types import TextBlock
from dotenv import load_dotenv

from trading.local_time import local_zone
from watchlist.sources.market_rundown import report_date_from_snapshot_file
from watchlist.sources.smb_gameplan import repository_day_dir

_WATCHLIST_PKG = Path(__file__).resolve().parent
_GUIDE_PATH = _WATCHLIST_PKG / 'prompts' / 'watchlist_report_guide.md'
_DEFAULT_MODEL = 'claude-sonnet-4-6'
_JSON_START_MARKER = 'WATCHLIST_JSON_V1'
_JSON_END_MARKER = 'END_WATCHLIST_JSON'
_SCHEMA_VERSION = 1
_MARKDOWN_TOP_ROWS = 8
_MOVERS_REQUIRED_KEYS = ('symbol', 'Catalyst', 'Bias', 'Why it matters', 'Key risk')
_RANKING_REQUIRED_KEYS = (
    'symbol',
    'Rank',
    'Total',
    'Direction',
    'Catalyst',
    'Move',
    'Market cap',
    'Short interest',
    'Volume %',
    'Technical',
)


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
    p_briefing_page_one: Path | None,
    p_market_rundown: Path | None,
) -> str:
    parts: list[str] = [
        f'Desk date (US trading day): **{desk_date.isoformat()}**.\n',
        'Produce the watchlist report using the guide below. Cite source filenames where helpful.\n',
        (
            'JSON contract reminder: `movers` rows must include keys '
            f'{", ".join(_MOVERS_REQUIRED_KEYS)}. '
            'JSON `ranking` rows must include keys '
            f'{", ".join(_RANKING_REQUIRED_KEYS)}. '
            'Use plain `Long` or `Short` in JSON Direction. '
            'Both `movers` and `ranking` must include every unique symbol from tickers_on_watchlist JSON.'
        ),
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
            'Briefing.com Page One (newest briefing_page_one_YYYY-MM-DD_*.txt for this desk date)',
            p_briefing_page_one,
            missing='no Briefing Page One snapshot for this date',
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


def _extract_report_text(message: Message) -> str:
    """Concatenate text blocks from a non-streaming ``messages.create`` result."""
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return '\n'.join(parts).strip()


def _split_json_and_markdown(report_payload: str) -> tuple[dict[str, object], str]:
    lines = report_payload.splitlines()
    if not lines or lines[0].strip() != _JSON_START_MARKER:
        raise SystemExit(f'model response must start with {_JSON_START_MARKER}')

    end_idx = next(idx for idx, line in enumerate(lines[1:], start=1) if line.strip() == _JSON_END_MARKER)

    json_text = '\n'.join(lines[1:end_idx]).strip()
    if not json_text:
        raise SystemExit('model response JSON section was empty')

    payload = json.loads(json_text)
    if not isinstance(payload, dict):
        raise SystemExit('JSON section must be a top-level object')

    markdown_text = '\n'.join(lines[end_idx + 1:]).strip()
    if not markdown_text:
        raise SystemExit('markdown section was empty after JSON envelope')
    return payload, markdown_text


def _extract_rows(
    payload: dict[str, object],
    key: str,
    *,
    required_keys: tuple[str, ...],
) -> list[dict[str, object]]:
    rows = cast('list[dict[str, object]]', payload[key])
    if not rows:
        raise SystemExit(f'JSON payload field `{key}` must include at least one row')

    for idx, row in enumerate(rows):
        missing = [required_key for required_key in required_keys if required_key not in row]
        if missing:
            raise SystemExit(
                f'JSON payload field `{key}` row {idx} missing required keys: {", ".join(missing)}',
            )
    return rows


def _symbols_in_rows(rows: list[dict[str, object]]) -> set[str]:
    return {str(row['symbol']).strip().upper() for row in rows}


def _load_watchlist_symbols(p_tickers: Path) -> set[str]:
    payload = cast('dict[str, object]', json.loads(p_tickers.read_text(encoding='utf-8')))
    tickers = cast('list[dict[str, object]]', payload['tickers'])
    symbols = {str(ticker['symbol']).strip().upper() for ticker in tickers}
    if not symbols:
        raise SystemExit(f'no symbols found in {p_tickers}')
    return symbols


def _as_int(value: object, *, key: str, symbol: str, default: int | None = None) -> int:
    if isinstance(value, int):
        return value
    value_text = str(value)
    try:
        return int(value_text)
    except ValueError as exc:
        if default is not None:
            print(f'warning: ranking row `{symbol}` has non-integer `{key}`: {value!r} — using {default}')
            return default
        raise SystemExit(f'ranking row `{symbol}` has non-integer `{key}`: {value!r}') from exc


_RANKING_SCORE_KEYS = (
    'Catalyst',
    'Move',
    'Market cap',
    'Short interest',
    'Volume %',
    'Technical',
)


def _normalize_ranking_totals(ranking_rows: list[dict[str, object]]) -> None:
    """Set ``Total`` to the sum of score columns when the model mismatched arithmetic."""
    for row in ranking_rows:
        symbol = str(row['symbol'])
        total_expected = sum(
            _as_int(row[score_key], key=score_key, symbol=symbol, default=0)
            for score_key in _RANKING_SCORE_KEYS
        )
        total_reported = _as_int(row['Total'], key='Total', symbol=symbol, default=0)
        if total_reported != total_expected:
            print(f'corrected ranking Total for `{symbol}`: {total_reported} -> {total_expected}')
            row['Total'] = total_expected


def _rerank_by_total(ranking_rows: list[dict[str, object]]) -> None:
    """Sort rows by Total descending and assign Rank 1, 2, 3, … in place.

    The model often assigns Rank values that do not match Total order; this
    corrects them after arithmetic is already normalised.
    """
    ranking_rows.sort(key=lambda r: _as_int(r['Total'], key='Total', symbol=str(r['symbol']), default=0), reverse=True)
    for idx, row in enumerate(ranking_rows, start=1):
        old_rank = row.get('Rank')
        if old_rank != idx:
            print(f'corrected ranking Rank for `{row["symbol"]}`: {old_rank} -> {idx}')
            row['Rank'] = idx


def _validate_sections_payload(
    payload: dict[str, object],
    desk_date: date,
    expected_symbols: set[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    desk_date_raw = payload.get('desk_date')
    if desk_date_raw != desk_date.isoformat():
        raise SystemExit(
            f'JSON payload `desk_date` must equal {desk_date.isoformat()} (got {desk_date_raw!r})',
        )

    movers_rows = _extract_rows(
        payload,
        'movers',
        required_keys=_MOVERS_REQUIRED_KEYS,
    )
    ranking_rows = _extract_rows(
        payload,
        'ranking',
        required_keys=_RANKING_REQUIRED_KEYS,
    )
    movers_symbols = _symbols_in_rows(movers_rows)
    ranking_symbols = _symbols_in_rows(ranking_rows)
    movers_rows = [row for row in movers_rows if str(row['symbol']).strip().upper() in expected_symbols]
    ranking_rows = [row for row in ranking_rows if str(row['symbol']).strip().upper() in expected_symbols]

    if not movers_rows:
        raise SystemExit('JSON payload `movers` had no rows matching watchlist symbols')
    if not ranking_rows:
        raise SystemExit('JSON payload `ranking` had no rows matching watchlist symbols')

    movers_skipped = sorted(movers_symbols - expected_symbols)
    ranking_skipped = sorted(ranking_symbols - expected_symbols)
    if movers_skipped:
        print(f'skipping non-watchlist movers symbols: {", ".join(movers_skipped)}')
    if ranking_skipped:
        print(f'skipping non-watchlist ranking symbols: {", ".join(ranking_skipped)}')

    _normalize_ranking_totals(ranking_rows)
    _rerank_by_total(ranking_rows)
    return movers_rows, ranking_rows


def _write_json_file(
    p_file: Path,
    content: dict[str, object],
) -> None:
    p_file.write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


def _md_cell(value: object) -> str:
    text = '' if value is None else str(value)
    return text.replace('|', r'\|').replace('\n', ' ').strip()


def _render_markdown_table(
    headers: list[str],
    rows: list[list[object]],
) -> str:
    header_row = '| ' + ' | '.join(headers) + ' |'
    separator_row = '|' + '|'.join('-' * (len(header) + 2) for header in headers) + '|'
    body_rows = ['| ' + ' | '.join(_md_cell(value) for value in row) + ' |' for row in rows]
    return '\n'.join([header_row, separator_row, *body_rows])


def _render_section_2_table(movers_rows: list[dict[str, object]]) -> str:
    headers = ['Symbol', 'Catalyst', 'Bias', 'Why it matters', 'Key risk']
    table_rows = [
        [row['symbol'], row['Catalyst'], row['Bias'], row['Why it matters'], row['Key risk']]
        for row in movers_rows
    ]
    return _render_markdown_table(headers, table_rows)


def _render_section_6_table(ranking_rows: list[dict[str, object]]) -> str:
    headers = [
        'Rank',
        'Ticker',
        'Direction',
        'Catalyst (0–40)',
        'Move (0–30)',
        'Market cap (0–10)',
        'Short interest (0–5)',
        'Volume % (0–10)',
        'Technical (0–10)',
        'Total',
    ]
    table_rows = [
        [
            row['Rank'],
            row['symbol'],
            row['Direction'],
            row['Catalyst'],
            row['Move'],
            row['Market cap'],
            row['Short interest'],
            row['Volume %'],
            row['Technical'],
            row['Total'],
        ]
        for row in ranking_rows
    ]
    return _render_markdown_table(headers, table_rows)


def _rank_sort_key(row: dict[str, object]) -> int:
    rank = row['Rank']
    return rank if isinstance(rank, int) else int(str(rank))


def _top_rows_for_markdown(
    movers_rows: list[dict[str, object]],
    ranking_rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    ranking_sorted = sorted(ranking_rows, key=_rank_sort_key)
    ranking_top = ranking_sorted[:_MARKDOWN_TOP_ROWS]
    top_symbols = [str(row['symbol']).strip().upper() for row in ranking_top]
    movers_by_symbol = {str(row['symbol']).strip().upper(): row for row in movers_rows}
    movers_top = [movers_by_symbol[symbol] for symbol in top_symbols if symbol in movers_by_symbol]
    return movers_top, ranking_top


def _find_section_header_start(report_body: str, section_number: int) -> int:
    """Index of the ``## Section N ...`` heading line, or ``-1`` if missing.

    Tolerant of separator/title variation after the number (em dash, hyphen,
    colon, etc.) so a single bad heading from the model does not abort the run.
    """
    match = re.search(
        rf'^##\s+Section\s+{section_number}\b',
        report_body,
        re.MULTILINE,
    )
    return -1 if match is None else match.start()


def _replace_section_table(
    report_body: str,
    section_number: int,
    table_markdown: str,
) -> str:
    start = _find_section_header_start(report_body, section_number)
    if start == -1:
        raise SystemExit(f'markdown missing `## Section {section_number}` header')

    body_start = report_body.find('\n', start)
    if body_start == -1:
        raise SystemExit(f'markdown `## Section {section_number}` header is malformed')
    body_start += 1

    next_section_match = re.search(r'^##\s+', report_body[body_start:], re.MULTILINE)
    section_end = body_start + next_section_match.start() if next_section_match else len(report_body)
    return report_body[:body_start] + '\n' + table_markdown + '\n\n' + report_body[section_end:].lstrip('\n')


def _colorize_ranking_direction(report_body: str) -> str:
    """Normalize Direction values in Section 6 ranking table."""
    start = _find_section_header_start(report_body, 6)
    if start == -1:
        return report_body

    body_start = report_body.find('\n', start)
    if body_start == -1:
        return report_body

    next_section_match = re.search(r'^##\s+', report_body[body_start:], re.MULTILINE)
    section_end = body_start + next_section_match.start() if next_section_match else len(report_body)
    section_text = report_body[start:section_end]

    section_text = re.sub(
        r'(\|\s*)(Long)(\s*\|)',
        r'\1🟢 Long\3',
        section_text,
    )
    section_text = re.sub(
        r'(\|\s*)(Short)(\s*\|)',
        r'\1🔴 Short\3',
        section_text,
    )

    return report_body[:start] + section_text + report_body[section_end:]


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
    p_briefing = _newest_matching(p_day, f'briefing_page_one_{desk_date.isoformat()}_*.txt')
    p_mr = _resolve_market_rundown_snapshot(p_day, desk_date, p_repository)

    print(f'desk_date={desk_date.isoformat()}')
    print(f'repository_dir={p_day}')
    print(f'guide={_GUIDE_PATH}')
    print(f'tickers_json={p_tickers} exists={p_tickers.is_file()}')
    print(f'gameplan={p_gameplan}')
    print(f'trader_tv={p_trader_tv}')
    print(f'briefing_page_one={p_briefing}')
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
        p_briefing_page_one=p_briefing,
        p_market_rundown=p_mr,
    )

    client = Anthropic(api_key=api_key)
    system_prompt = (
        'You are an assistant for a US day trader. Follow the guide document in the user '
        'message exactly for structure, headings, and rules. Do not invent tickers or '
        'catalysts; use only the supplied sources. Output exactly two sections in one response: '
        f'line 1 is `{_JSON_START_MARKER}`, then valid JSON only, then a line with '
        f'`{_JSON_END_MARKER}`, then the full markdown report. Do not use markdown fences.'
    )
    with client.messages.stream(
        model=model,
        max_tokens=32768,
        system=system_prompt,
        messages=[{'role': 'user', 'content': user_prompt}],
    ) as stream:
        response = stream.get_final_message()
    if not isinstance(response, Message):
        raise SystemExit('expected Message from Anthropic stream')
    report_payload = _extract_report_text(response)
    if not report_payload:
        raise SystemExit('empty response from Anthropic')
    payload, report_body = _split_json_and_markdown(report_payload)
    expected_symbols = _load_watchlist_symbols(p_tickers)
    movers_rows, ranking_rows = _validate_sections_payload(payload, desk_date, expected_symbols)
    movers_top, ranking_top = _top_rows_for_markdown(movers_rows, ranking_rows)
    report_body = _replace_section_table(
        report_body,
        2,
        _render_section_2_table(movers_top),
    )
    report_body = _replace_section_table(
        report_body,
        6,
        _render_section_6_table(ranking_top),
    )
    report_body = _colorize_ranking_direction(report_body)

    generated_local = datetime.now(local_zone())
    stamp = generated_local.strftime('%H%M%S')
    p_out = p_day / f'watchlist_report_{desk_date.isoformat()}_{stamp}.md'
    p_movers = p_day / f'movers_{desk_date.isoformat()}.json'
    p_ranking = p_day / f'ranking_{desk_date.isoformat()}.json'
    p_day.mkdir(parents=True, exist_ok=True)

    json_metadata: dict[str, object] = {
        'schema_version': _SCHEMA_VERSION,
        'desk_date': desk_date.isoformat(),
        'model': model,
        'generated_local': generated_local.isoformat(),
        'report_filename': p_out.name,
    }
    _write_json_file(
        p_movers,
        {
            **json_metadata,
            'section': 'Section 2 — Movers',
            'rows': movers_rows,
        },
    )
    _write_json_file(
        p_ranking,
        {
            **json_metadata,
            'section': 'Section 6 — Ranking',
            'rows': ranking_rows,
        },
    )

    header = (
        f'<!-- desk_date={desk_date.isoformat()} model={model} '
        f'generated_local={generated_local.isoformat()} -->\n\n'
    )
    p_out.write_text(header + report_body + '\n', encoding='utf-8')
    print(f'wrote {p_movers}')
    print(f'wrote {p_ranking}')
    print(f'wrote {p_out}')


if __name__ == '__main__':
    main()
