"""Massive REST: stock minute aggregates to a bars DataFrame.

See ``docs/massive_rest_stocks_custom_bars.md`` for the Custom Bars (OHLC) contract:
``GET /v2/aggs/ticker/{stocksTicker}/range/{multiplier}/{timespan}/{from}/{to}`` on
``MASSIVE_REST_BASE_URL`` (default ``https://api.massive.com``).

Aggregate GETs retry on HTTP 429 / 5xx and on connection-level errors, with exponential
backoff and optional ``Retry-After`` (seconds or HTTP-date). Pagination state is
unchanged: only the failed request is retried.
"""

import logging
import random
import time
from collections.abc import Mapping
from datetime import UTC
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import cast
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.parse import urlunparse

import numpy as np
import pandas as pd
import requests

from trading import config as cf
from trading.storage.ohlcv.ohlcv_schema import BAR_FRAME_COLUMNS
from trading.storage.ohlcv.ohlcv_schema import empty_bars_dataframe

log = logging.getLogger(__name__)

# Retries apply per GET (first page or each ``next_url``); pagination resumes after success.
MASSIVE_HTTP_MAX_ATTEMPTS = 12
MASSIVE_HTTP_BASE_BACKOFF_S = 1.0
MASSIVE_HTTP_MAX_BACKOFF_S = 120.0
MASSIVE_HTTP_RETRY_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Aggregate bar fields read from Massive JSON (``vw`` is intentionally omitted).
MASSIVE_AGG_BAR_FIELDS: tuple[str, ...] = ('t', 'o', 'h', 'l', 'c', 'v')


def _require_massive_api_key() -> str:
    key = cf.MASSIVE_API_KEY.strip()
    if not key:
        msg = 'Set non-empty MASSIVE_API_KEY in the environment'
        raise ValueError(msg)
    return key


def _url_with_api_key(url: str, api_key: str) -> str:
    """Ensure ``apiKey`` query param is present (Massive REST style)."""
    parts = urlparse(url)
    query_pairs = dict(parse_qsl(parts.query, keep_blank_values=True))
    if 'apiKey' not in query_pairs:
        query_pairs['apiKey'] = api_key
    new_query = urlencode(query_pairs)
    return urlunparse(
        (parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment),
    )


def _massive_backoff_sleep_s(response: requests.Response | None, attempt: int) -> float:
    """Sleep duration before retry: honor ``Retry-After`` when sensible, else exponential + jitter."""
    if response is not None:
        ra_raw = response.headers.get('Retry-After')
        if ra_raw is not None:
            ra = ra_raw.strip()
            if ra.isdigit():
                return min(MASSIVE_HTTP_MAX_BACKOFF_S, float(ra))
            try:
                retry_dt = parsedate_to_datetime(ra)
            except (OSError, TypeError, ValueError):
                retry_dt = None
            if retry_dt is not None:
                if retry_dt.tzinfo is None:
                    retry_dt = retry_dt.replace(tzinfo=UTC)
                delta_s = (retry_dt.astimezone(UTC) - datetime.now(UTC)).total_seconds()
                return min(MASSIVE_HTTP_MAX_BACKOFF_S, max(0.0, delta_s))
    exp = min(
        MASSIVE_HTTP_MAX_BACKOFF_S,
        MASSIVE_HTTP_BASE_BACKOFF_S * (2 ** attempt),
    )
    jitter = random.uniform(0.0, 0.25 * exp)
    return exp + jitter


def _fetch_aggs_session(
        session: requests.Session,
        url: str,
        api_key: str,
        *,
        timeout_s: float = 60.0,
) -> Mapping[str, object]:
    full_url = _url_with_api_key(url, api_key)
    last_response: requests.Response | None = None
    for attempt in range(MASSIVE_HTTP_MAX_ATTEMPTS):
        try:
            response = session.get(full_url, timeout=timeout_s)
        except requests.exceptions.RequestException as exc:
            sleep_s = _massive_backoff_sleep_s(None, attempt)
            log.warning(
                'Massive aggs request error %s attempt %s/%s sleeping_s=%.2f url=%s',
                type(exc).__name__,
                attempt + 1,
                MASSIVE_HTTP_MAX_ATTEMPTS,
                sleep_s,
                full_url[:160],
            )
            time.sleep(sleep_s)
            continue
        last_response = response
        if response.status_code in MASSIVE_HTTP_RETRY_STATUS:
            sleep_s = _massive_backoff_sleep_s(response, attempt)
            log.warning(
                'Massive aggs HTTP %s attempt %s/%s sleeping_s=%.2f url=%s',
                response.status_code,
                attempt + 1,
                MASSIVE_HTTP_MAX_ATTEMPTS,
                sleep_s,
                response.url[:160],
            )
            time.sleep(sleep_s)
            continue
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            msg = f'Expected JSON object from Massive aggs, got {type(data).__name__}'
            raise TypeError(msg)
        return cast('Mapping[str, object]', data)

    if last_response is not None:
        last_response.raise_for_status()
    msg = f'Massive aggs exhausted {MASSIVE_HTTP_MAX_ATTEMPTS} attempts without HTTP response'
    raise requests.HTTPError(msg)


def _json_number(raw: object) -> float:
    """Coerce Massive aggs numeric JSON (``int`` / ``float``) to ``float``."""
    return float(cast('int | float', raw))


def _append_massive_agg_row(
        r: Mapping[str, object],
        *,
        t_list: list[float],
        o_list: list[float],
        h_list: list[float],
        l_list: list[float],
        c_list: list[float],
        v_list: list[float],
) -> None:
    """Append one aggregate row using :data:`MASSIVE_AGG_BAR_FIELDS` only (never ``vw``)."""
    lists = (t_list, o_list, h_list, l_list, c_list, v_list)
    for field, lst in zip(MASSIVE_AGG_BAR_FIELDS, lists, strict=True):
        lst.append(_json_number(r[field]))


def massive_minute_aggs_first_page_url(
        symbol: str,
        start: datetime,
        end: datetime,
        *,
        interval_minutes: int = 1,
        base_url: str | None = None,
) -> str:
    """Build the first-page Massive aggregates GET URL (``apiKey`` added at request time).

    Path shape: ``/v2/aggs/ticker/{SYM}/range/1/minute/{from_ms}/{to_ms}`` with
    ``limit=50000&sort=asc`` query (before ``apiKey``).
    """
    if interval_minutes != 1:
        msg = 'massive_minute_aggs_first_page_url only supports interval_minutes=1'
        raise ValueError(msg)
    base = (base_url or cf.MASSIVE_REST_BASE_URL).strip().rstrip('/')
    sym = symbol.strip().upper()
    start_utc = start.astimezone(UTC) if start.tzinfo else start.replace(tzinfo=UTC)
    end_utc = end.astimezone(UTC) if end.tzinfo else end.replace(tzinfo=UTC)
    from_ms = int(start_utc.timestamp() * 1000)
    to_ms = int(end_utc.timestamp() * 1000)
    path = f'/v2/aggs/ticker/{sym}/range/1/minute/{from_ms}/{to_ms}'
    return f'{base}{path}?limit=50000&sort=asc'


def fetch_stock_minute_bars_dataframe(
        symbol: str,
        start: datetime,
        end: datetime,
        *,
        interval_minutes: int = 1,
        session: requests.Session | None = None,
        timeout_s: float = 60.0,
) -> pd.DataFrame:
    """Fetch 1-minute aggregates for one ticker; return Alpaca-shaped bar columns.

    Uses Massive custom bars (aggregates) path
    (``/v2/aggs/ticker/.../range/...``). ``start``/``end``
    are converted to UTC millisecond bounds in the URL.

    Output ``vwap`` is always NaN. Massive JSON field ``vw`` (per-minute VWAP) is never read;
    session VWAP is computed later from OHLCV (see ``IndicatorPipeline`` / ``vwap_series``).

    Rows are assembled column-wise (one pass per page over ``results``). Sorting is
    left to ``merge_and_write`` so ingest avoids a redundant full-frame sort here.

    Parameters
    ----------
    symbol
        Equity ticker (e.g. ``AAPL``).
    start, end
        Inclusive-ish window; both should be timezone-aware for correct UTC instants.
    interval_minutes
        Must be ``1`` for this helper (path uses ``minute`` timespan).
    session
        Optional shared ``requests.Session`` for connection reuse.
    """
    if interval_minutes != 1:
        msg = 'fetch_stock_minute_bars_dataframe only supports interval_minutes=1'
        raise ValueError(msg)

    api_key = _require_massive_api_key()
    sym = symbol.strip().upper()
    url = massive_minute_aggs_first_page_url(sym, start, end, interval_minutes=1)

    own_session = session is None
    sess = session or requests.Session()

    t_list: list[float] = []
    o_list: list[float] = []
    h_list: list[float] = []
    l_list: list[float] = []
    c_list: list[float] = []
    v_list: list[float] = []

    try:
        payload = _fetch_aggs_session(sess, url, api_key, timeout_s=timeout_s)
        while True:
            status = payload.get('status')
            if status == 'ERROR':
                msg = str(payload.get('message', payload))
                raise ValueError(msg)
            if status == 'NOT_FOUND':
                break
            raw_results = payload.get('results')
            results = raw_results if isinstance(raw_results, list) else []
            for item in results:
                if not isinstance(item, dict):
                    continue
                r = cast('Mapping[str, object]', item)
                _append_massive_agg_row(
                    r,
                    t_list=t_list,
                    o_list=o_list,
                    h_list=h_list,
                    l_list=l_list,
                    c_list=c_list,
                    v_list=v_list,
                )
            next_url = payload.get('next_url')
            if not next_url or not isinstance(next_url, str):
                break
            payload = _fetch_aggs_session(sess, next_url, api_key, timeout_s=timeout_s)
    finally:
        if own_session:
            sess.close()

    if not t_list:
        return empty_bars_dataframe()

    n = len(t_list)
    df_bars = pd.DataFrame(
        {
            'symbol': [sym] * n,
            'interval': np.full(n, interval_minutes, dtype=np.int64),
            'timestamp': pd.to_datetime(t_list, unit='ms', utc=True),
            'open': o_list,
            'high': h_list,
            'low': l_list,
            'close': c_list,
            'volume': v_list,
            'vwap': np.full(n, np.nan, dtype=np.float64),
        },
    )
    return df_bars.loc[:, list(BAR_FRAME_COLUMNS)]
