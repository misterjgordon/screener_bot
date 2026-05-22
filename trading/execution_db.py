"""Insert execution rows into database_smb PostgreSQL.

Uses Django ORM. Timestamps from format_timestamp() are America/Vancouver wall clock;
_parse_timestamp interprets those strings as Vancouver and converts to UTC naive for
storage (matching import_executions semantics).

Rows land in table ``executions`` (see ``Execution`` model). Failures are logged at
WARNING/ERROR; the screener does not raise on DB errors.
"""

import logging
import os
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation

import django

from trading.market_timezones import UTC
from trading.market_timezones import local_zone

if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smbweb.settings')
    django.setup()

from smbweb.apps.executions.models import Execution
from trading.models import round_money_2
from trading.record_executions import format_timestamp

logger = logging.getLogger(__name__)


def _parse_timestamp(timestamp_str: str) -> datetime | None:
    """
    Parse timestamp string as America/Vancouver local, return UTC naive datetime.

    Matches import_executions semantics for consistency between real-time inserts
    and CSV imports.
    """
    if not timestamp_str or not timestamp_str.strip():
        return None
    s = ' '.join(timestamp_str.strip().split())
    for suffix in [' PST', ' PDT', ' UTC', ' EST', ' EDT', ' CST', ' CDT', ' MST', ' MDT']:
        if s.endswith(suffix):
            s = s[:-len(suffix)].strip()
            break
    for fmt in [
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%f',
        '%Y-%m-%dT%H:%M',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d %I:%M:%S %p',
        '%Y-%m-%d %I:%M %p',
        '%Y-%m-%d',
    ]:
        try:
            dt = datetime.strptime(s, fmt)
            dt_local = dt.replace(tzinfo=local_zone())
            dt_utc = dt_local.astimezone(UTC)
            return dt_utc.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _to_decimal(val: float | None) -> Decimal | None:
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None


def save_execution_to_db(
    trader: str,
    symbol: str,
    change_type: str,
    net_side: str,
    delta_magnitude: float,
    entry_price: float | None = None,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    order_id: str | None = None,
    filled_price: float | None = None,
    timestamp: str | None = None,
    shares: int | None = None,
    total_risk: float | None = None,
    risk_per_share: float | None = None,
    risk_percent: float | None = None,
    skip_duplicates: bool = True,
) -> None:
    """
    Insert execution row into database_smb.

    On DB error, logs and returns without raising so trading continues.
    """
    if timestamp is None:
        timestamp = format_timestamp()
    dt = _parse_timestamp(timestamp)
    if dt is None:
        logger.warning(
            'Skipping execution DB insert: could not parse timestamp %r',
            timestamp,
        )
        return

    order_id_str = str(order_id).strip() if order_id is not None else None
    if order_id_str == '':
        order_id_str = None

    if skip_duplicates and Execution.objects.filter(  # type: ignore[attr-defined]
        timestamp=dt,
        trader=trader.strip(),
        symbol=symbol.strip(),
        change_type=change_type.strip(),
    ).exists():
        logger.info(
            'Skipping execution DB insert (duplicate): %s %s %s @ %s',
            trader.strip(),
            symbol.strip(),
            change_type.strip(),
            dt.isoformat(sep=' '),
        )
        return

    market_value: float | None = None
    if shares is not None and (entry_price is not None or filled_price is not None):
        price = entry_price if entry_price is not None else filled_price
        if price is not None:
            market_value = round_money_2(float(shares) * float(price))

    total_risk_r = round_money_2(total_risk)
    risk_per_share_r = round_money_2(risk_per_share)
    risk_percent_r = round_money_2(risk_percent)

    try:
        Execution.objects.create(  # type: ignore[attr-defined]
            timestamp=dt,
            trader=trader.strip(),
            symbol=symbol.strip()[:20],
            change_type=change_type.strip(),
            net_side=net_side.strip(),
            delta_magnitude=float(delta_magnitude),
            entry_price=_to_decimal(entry_price),
            stop_price=_to_decimal(stop_price),
            take_profit_price=_to_decimal(take_profit_price),
            order_id=order_id_str,
            filled_price=_to_decimal(filled_price),
            shares=shares,
            total_risk=total_risk_r,
            risk_per_share=risk_per_share_r,
            market_value=market_value,
            risk_percent=risk_percent_r,
        )
        logger.info(
            'Saved execution to DB: %s %s %s @ %s',
            trader.strip(),
            symbol.strip(),
            change_type.strip(),
            dt.isoformat(sep=' '),
        )
    except Exception:
        logger.exception(
            'Execution DB insert failed (trading continues): %s %s %s',
            trader,
            symbol,
            change_type,
        )
