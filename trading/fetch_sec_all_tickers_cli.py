"""Download SEC ``company_tickers_exchange.json`` to ``all_tickers.json``.

The SEC publishes a consolidated JSON ticker/exchange listing (same schema as
:class:`~trading.models.SecTickers`). This feed covers identifiers and venue
names — **not** fundamentals (revenue, earnings, ratios).

For fundamentals tied to SEC filings, use EDGAR **companyfacts** / XBRL APIs per
CIK once you have a symbol→CIK map from this file. Commercial consolidated APIs
(e.g. Polygon financials, Financial Modeling Prep, Finnhub) bundle listings plus
fundamentals behind keys.

SEC requests an identifiable ``User-Agent``. Resolution order: ``--user-agent``,
then ``SEC_HTTP_USER_AGENT``, then ``SEC_HTTP_CONTACT_EMAIL`` (becomes
``trading-research <email>``), then ``git config user.email`` (same pattern).

See https://www.sec.gov/about/developer-resources

Examples
--------
::

    uv run --frozen python -m trading.fetch_sec_all_tickers_cli

::

    SEC_HTTP_USER_AGENT='MyBot contact@example.com' uv run --frozen python -m trading.fetch_sec_all_tickers_cli

Via scripts launcher::

    uv run --frozen python scripts/fetch_sec_all_tickers.py
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import requests

from trading.config import SEC_HTTP_CONTACT_EMAIL
from trading.config import SEC_HTTP_USER_AGENT
from trading.models import SecTickers
from trading.models import p_all_tickers_json_path

SEC_COMPANY_TICKERS_EXCHANGE_URL = 'https://www.sec.gov/files/company_tickers_exchange.json'


def _git_config_user_email() -> str:
    """Return ``git config user.email`` or empty if unavailable."""
    try:
        proc = subprocess.run(
            ['git', 'config', '--get', 'user.email'],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, TimeoutError):
        return ''
    if proc.returncode != 0:
        return ''
    return proc.stdout.strip()


def resolve_sec_http_user_agent(cli_user_agent: str | None) -> str:
    """Pick User-Agent for SEC requests (fair access).

    Non-empty ``--user-agent`` wins; otherwise env and git fallbacks apply.
    """
    if cli_user_agent is not None:
        from_cli = cli_user_agent.strip()
        if from_cli:
            return from_cli
    if SEC_HTTP_USER_AGENT:
        return SEC_HTTP_USER_AGENT
    if SEC_HTTP_CONTACT_EMAIL:
        return f'trading-research {SEC_HTTP_CONTACT_EMAIL}'
    email_git = _git_config_user_email()
    if email_git:
        return f'trading-research ({email_git})'
    return ''


def _atomic_write_text(p_dest: Path, text: str) -> None:
    """Write UTF-8 text to ``p_dest`` via a same-directory replace."""
    p_dest.parent.mkdir(parents=True, exist_ok=True)
    p_tmp = p_dest.with_suffix(p_dest.suffix + '.tmp')
    p_tmp.write_text(text, encoding='utf-8')
    p_tmp.replace(p_dest)


def fetch_sec_company_tickers_exchange(
        *,
        url: str,
        user_agent: str,
        timeout_seconds: float,
) -> str:
    """GET the SEC JSON document and return decoded body text.

    Parameters
    ----------
    url
        SEC ``company_tickers_exchange.json`` URL (or mirror for tests).
    user_agent
        Required descriptive User-Agent string (SEC fair access).
    timeout_seconds
        HTTP read timeout.

    Returns
    -------
    str
        Response body (JSON text).

    Raises
    ------
    requests.HTTPError
        Non-success HTTP status.
    """
    headers = {'User-Agent': user_agent}
    response = requests.get(url, headers=headers, timeout=timeout_seconds)
    response.raise_for_status()
    response.encoding = response.encoding or 'utf-8'
    return response.text


def validate_sec_ticker_json_text(text: str) -> SecTickers:
    """Parse and validate ticker JSON using :class:`~trading.models.SecTickers`."""
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise TypeError('SEC ticker JSON root must be an object')
    return SecTickers.from_json_dict(obj)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: download, validate, write ``all_tickers.json``."""
    parser = argparse.ArgumentParser(
        description='Download SEC company_tickers_exchange.json into trading/data/symbols/all_tickers.json.',
    )
    parser.add_argument(
        '--output',
        '-o',
        type=Path,
        default=None,
        help=f'Output path (default: {p_all_tickers_json_path()})',
    )
    parser.add_argument(
        '--url',
        default=SEC_COMPANY_TICKERS_EXCHANGE_URL,
        help='Override SEC JSON URL (default: official SEC files URL)',
    )
    parser.add_argument(
        '--user-agent',
        default=None,
        metavar='STR',
        help=(
            'HTTP User-Agent (default: SEC_HTTP_USER_AGENT, then SEC_HTTP_CONTACT_EMAIL, '
            'then git user.email)'
        ),
    )
    parser.add_argument(
        '--timeout',
        type=float,
        default=120.0,
        help='HTTP timeout seconds (default: 120)',
    )
    ns = parser.parse_args(argv)

    user_agent = resolve_sec_http_user_agent(ns.user_agent)
    if not user_agent:
        print(
            'SEC requests an identifiable User-Agent. Set SEC_HTTP_USER_AGENT, or '
            'SEC_HTTP_CONTACT_EMAIL, or git config user.email, or pass --user-agent.',
            file=sys.stderr,
        )
        return 1

    p_out = ns.output if ns.output is not None else p_all_tickers_json_path()
    text = fetch_sec_company_tickers_exchange(
        url=ns.url,
        user_agent=user_agent,
        timeout_seconds=ns.timeout,
    )
    bundle = validate_sec_ticker_json_text(text)
    _atomic_write_text(p_out, text)
    print(f'Wrote {len(bundle.rows)} rows to {p_out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
