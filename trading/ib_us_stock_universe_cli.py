"""Fetch a broad US equity symbol list via Interactive Brokers market scanners.

IB does not provide Alpaca-style ``get_all_assets``. The supported approach is
``reqScannerData`` with ``instrument='STK'`` and a US ``locationCode``. Each
scan returns **at most** ``numberOfRows`` rows (IB caps this at 50 per scan).

This script **paginates** by splitting the price axis into bands and by using
multiple scan codes, then unions and dedupes results by ``conId``. Coverage is
much broader than a single scan but **not** a complete listing of every US name.

Examples
--------
::

    uv run --frozen python -m trading.ib_us_stock_universe_cli --output /tmp/ib_us.csv

Or use the scripts launcher (adds repo root to ``sys.path``)::

    uv run --frozen python scripts/ib_fetch_us_stock_universe.py --output /tmp/ib_us.csv
"""

import argparse
import csv
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ib_async import IB
from ib_async import Contract
from ib_async import ScannerSubscription

from trading.config import IB_CLIENT_ID_MARKET_DATA
from trading.config import IB_HOST
from trading.config import IB_PORT
from trading.market_data import connect
from trading.market_data import disconnect

if TYPE_CHECKING:
    from ib_async.contract import ScanData

LOG = logging.getLogger(__name__)

# Major US listings (NYSE/NASDAQ/ARCA subset); use ``STK.US`` for broader US stocks.
DEFAULT_LOCATION_CODE = 'STK.US.MAJOR'

# Scanner codes known to work on many setups; disabled codes yield empty data (skipped).
DEFAULT_SCAN_CODES: tuple[str, ...] = (
    'HOT_BY_VOLUME',
    'MOST_ACTIVE',
    'TOP_PERC_GAIN',
    'HIGH_OPEN_GAP',
    'LOW_OPEN_GAP',
)

# Price bands (USD); each band is (abovePrice, belowPrice) inclusive of filter semantics.
DEFAULT_PRICE_BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 1.0),
    (1.0, 2.5),
    (2.5, 5.0),
    (5.0, 10.0),
    (10.0, 20.0),
    (20.0, 50.0),
    (50.0, 100.0),
    (100.0, 250.0),
    (250.0, 500.0),
    (500.0, 1000.0),
    (1000.0, 5000.0),
    (5000.0, 50_000.0),
    (50_000.0, 1e9),
)

MAX_SCANNER_ROWS = 50


def _parse_bands(spec: str | None) -> tuple[tuple[float, float], ...]:
    """Parse ``'lo:hi,lo:hi'`` into band tuples (colon separators)."""
    if not spec or not spec.strip():
        return DEFAULT_PRICE_BANDS
    bands: list[tuple[float, float]] = []
    for raw_chunk in spec.split(','):
        chunk = raw_chunk.strip()
        if not chunk:
            continue
        parts = chunk.replace('-', ':').split(':')
        if len(parts) != 2:
            msg = f'Bad band (need lo:hi): {chunk!r}'
            raise ValueError(msg)
        lo, hi = float(parts[0]), float(parts[1])
        bands.append((lo, hi))
    if not bands:
        return DEFAULT_PRICE_BANDS
    return tuple(bands)


def _scan_data_to_record(row: 'ScanData') -> dict[str, object]:
    """Flatten ``ScanData`` into CSV-friendly fields."""
    cd = row.contractDetails
    c: Contract | None = cd.contract
    if c is None:
        return {
            'con_id': 0,
            'symbol': '',
            'currency': '',
            'sec_type': '',
            'exchange': '',
            'primary_exchange': '',
            'local_symbol': '',
            'trading_class': '',
            'long_name': (cd.longName or '').strip(),
        }

    return {
        'con_id': int(c.conId) if c.conId else 0,
        'symbol': (c.symbol or '').strip().upper(),
        'currency': (c.currency or '').strip(),
        'sec_type': (c.secType or '').strip(),
        'exchange': (c.exchange or '').strip(),
        'primary_exchange': (c.primaryExchange or '').strip(),
        'local_symbol': (c.localSymbol or '').strip(),
        'trading_class': (c.tradingClass or '').strip(),
        'long_name': (cd.longName or '').strip(),
    }


def _merge_source(existing_sources: str, new_tag: str) -> str:
    """Append ``new_tag`` to semicolon-separated sources without duplicates."""
    parts = [p for p in existing_sources.split(';') if p.strip()]
    if new_tag not in parts:
        parts.append(new_tag)
    return ';'.join(parts)


def fetch_us_listed_scanner(
        ib: IB,
        *,
        location_code: str,
        scan_codes: Sequence[str],
        price_bands: Sequence[tuple[float, float]],
        sleep_seconds: float,
        number_of_rows: int,
) -> dict[int, dict[str, object]]:
    """Run scanner grid (bands × scan codes) and merge by ``conId``.

    Parameters
    ----------
    ib
        Connected ``IB`` instance.
    location_code
        e.g. ``STK.US.MAJOR`` or ``STK.US``.
    scan_codes
        IB scan codes (see ``reqScannerParameters`` XML or TWS scanner docs).
    price_bands
        ``(above_price, below_price)`` pairs passed to ``ScannerSubscription``.
    sleep_seconds
        Pause between requests to reduce pacing / subscription churn.
    number_of_rows
        Rows per scan (IB maximum effective size is 50).

    Returns
    -------
    dict
        ``conId`` → row dict including merged ``sources`` and ``source_count``.
    """
    rows_cap = min(number_of_rows, MAX_SCANNER_ROWS)
    merged: dict[int, dict[str, object]] = {}
    for band_lo, band_hi in price_bands:
        for scan_code in scan_codes:
            sub = ScannerSubscription(
                numberOfRows=rows_cap,
                instrument='STK',
                locationCode=location_code,
                scanCode=scan_code,
                abovePrice=band_lo,
                belowPrice=band_hi,
            )
            tag = f'{scan_code}[{band_lo:g}-{band_hi:g}]'
            try:
                scan_list = ib.reqScannerData(sub)
            except Exception as exc:
                LOG.warning('reqScannerData failed for %s: %s', tag, exc)
                time.sleep(sleep_seconds)
                continue

            if not scan_list:
                LOG.debug('Empty scan: %s', tag)
                time.sleep(sleep_seconds)
                continue

            for scan_row in scan_list:
                rec = _scan_data_to_record(scan_row)
                con_id = rec['con_id']
                if not isinstance(con_id, int) or con_id <= 0:
                    continue
                sym = rec['symbol']
                if not sym:
                    continue

                if con_id not in merged:
                    rec['sources'] = tag
                    rec['source_count'] = 1
                    merged[con_id] = rec
                else:
                    prev = merged[con_id]
                    prev_sources = str(prev.get('sources', ''))
                    merged_sources = _merge_source(prev_sources, tag)
                    prev['sources'] = merged_sources
                    prev['source_count'] = len(
                        [p for p in merged_sources.split(';') if p.strip()],
                    )

            time.sleep(sleep_seconds)

    return merged


def _sort_row_key(row_dict: dict[str, object]) -> tuple[str, int]:
    """Stable CSV ordering: symbol then ``con_id``."""
    sym = str(row_dict.get('symbol', ''))
    raw_id = row_dict.get('con_id', 0)
    if isinstance(raw_id, int):
        con_id = raw_id
    elif isinstance(raw_id, str) and raw_id.isdigit():
        con_id = int(raw_id)
    else:
        con_id = 0
    return sym, con_id


def _write_csv(p_output: Path, rows: Sequence[dict[str, object]]) -> None:
    """Write merged rows to UTF-8 CSV."""
    fieldnames = [
        'con_id',
        'symbol',
        'currency',
        'sec_type',
        'exchange',
        'primary_exchange',
        'local_symbol',
        'trading_class',
        'long_name',
        'source_count',
        'sources',
    ]
    with p_output.open('w', newline='', encoding='utf-8') as f_csv:
        writer = csv.DictWriter(f_csv, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for row_dict in sorted(rows, key=_sort_row_key):
            writer.writerow({k: row_dict.get(k, '') for k in fieldnames})


def main(argv: list[str] | None = None) -> int:
    """CLI entry: connect, fetch scanner union, write CSV."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

    parser = argparse.ArgumentParser(description='IB US stock universe via scanners (union / dedupe).')
    parser.add_argument(
        '--output',
        '-o',
        required=True,
        help='Output CSV path',
    )
    parser.add_argument(
        '--host',
        default=None,
        help='Override IB host (default: trading.config.IB_HOST)',
    )
    parser.add_argument(
        '--port',
        type=int,
        default=None,
        help='Override IB port (default: trading.config.IB_PORT)',
    )
    parser.add_argument(
        '--client-id',
        type=int,
        default=IB_CLIENT_ID_MARKET_DATA,
        help=f'IB client id (default: {IB_CLIENT_ID_MARKET_DATA})',
    )
    parser.add_argument(
        '--location-code',
        default=DEFAULT_LOCATION_CODE,
        help=f'US scanner location (default: {DEFAULT_LOCATION_CODE})',
    )
    parser.add_argument(
        '--scan-codes',
        default=','.join(DEFAULT_SCAN_CODES),
        help='Comma-separated scan codes',
    )
    parser.add_argument(
        '--bands',
        default='',
        help='Comma-separated price bands as lo:hi (default: built-in grid). Example: 0:5,5:10,10:50',
    )
    parser.add_argument(
        '--sleep',
        type=float,
        default=0.35,
        help='Seconds to sleep between scanner requests (default: 0.35)',
    )
    parser.add_argument(
        '--number-of-rows',
        type=int,
        default=MAX_SCANNER_ROWS,
        help=f'Max rows per scan (default: {MAX_SCANNER_ROWS}, IB caps near 50)',
    )

    ns = parser.parse_args(argv)
    scan_codes = tuple(s.strip() for s in ns.scan_codes.split(',') if s.strip())
    if not scan_codes:
        LOG.error('No scan codes after parsing')
        return 1

    try:
        bands = _parse_bands(ns.bands or None)
    except ValueError as exc:
        LOG.error('%s', exc)
        return 1

    host = ns.host if ns.host is not None else IB_HOST
    port = ns.port if ns.port is not None else IB_PORT
    ib = connect(host=host, port=port, client_id=ns.client_id, readonly=True)
    if ib is None or not ib.isConnected():
        LOG.error('Could not connect to IB (TWS / Gateway running?)')
        return 1

    try:
        LOG.info(
            'Fetching %d bands × %d scans (~%d requests); results deduped by conId',
            len(bands),
            len(scan_codes),
            len(bands) * len(scan_codes),
        )
        merged = fetch_us_listed_scanner(
            ib,
            location_code=ns.location_code,
            scan_codes=scan_codes,
            price_bands=bands,
            sleep_seconds=ns.sleep,
            number_of_rows=ns.number_of_rows,
        )
    finally:
        disconnect(ib)

    rows_out = list(merged.values())
    p_out = Path(ns.output)
    _write_csv(p_out, rows_out)
    LOG.info('Wrote %d unique contracts to %s', len(rows_out), p_out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
