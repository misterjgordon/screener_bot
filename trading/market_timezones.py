"""Market and backtest timezones: IANA names, ZoneInfo, and UTC offset helpers.

Canonical zone names live in :data:`P_MARKET_TIMEZONES_CONFIG` (``market_timezones.yaml``).
Override per role with env ``EXCHANGE_TZ``, ``DISPLAY_TZ``, or ``LOCAL_TZ`` (any name in ``registry``).

Bars and Parquet stay UTC; convert at boundaries (load windows, ``trading_date``, CLI tables).
Strategy session gates use per-strategy YAML ``timezone`` (usually the exchange zone).
"""

import os
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import UTC
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

P_MARKET_TIMEZONES_CONFIG = Path(__file__).resolve().parent / 'market_timezones.yaml'

_ROLE_ENV_KEYS: dict[str, str] = {
    'exchange': 'EXCHANGE_TZ',
    'display': 'DISPLAY_TZ',
    'local': 'LOCAL_TZ',
}

UTC = UTC


@lru_cache(maxsize=1)
def _config() -> dict[str, object]:
    with P_MARKET_TIMEZONES_CONFIG.open(encoding='utf-8') as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def registered_timezone_names() -> frozenset[str]:
    """IANA names allowed for env overrides and :func:`zone`."""
    registry = _config()['registry']
    if not isinstance(registry, list):
        msg = 'market_timezones.yaml registry must be a list'
        raise TypeError(msg)
    return frozenset(str(name) for name in registry)


def _role_timezone_name(role: str) -> str:
    roles = _config()['roles']
    if not isinstance(roles, dict):
        msg = 'market_timezones.yaml roles must be a mapping'
        raise TypeError(msg)
    if role not in _ROLE_ENV_KEYS:
        msg = f'Unknown timezone role {role!r}; roles={sorted(_ROLE_ENV_KEYS)}'
        raise ValueError(msg)
    default = str(roles[role])
    env_key = _ROLE_ENV_KEYS[role]
    override = os.environ.get(env_key, '').strip()
    name = override or default
    if name not in registered_timezone_names():
        msg = f'Unknown timezone {name!r} (role={role}); registry={sorted(registered_timezone_names())}'
        raise ValueError(msg)
    return name


def exchange_timezone_name() -> str:
    """US equity session calendar and cold-load ET bounds (default ``America/New_York``)."""
    return _role_timezone_name('exchange')


def display_timezone_name() -> str:
    """Backtest CLI / desk bar tables (default ``America/Los_Angeles``)."""
    return _role_timezone_name('display')


def local_timezone_name() -> str:
    """Developer desk / machine wall clock (default ``America/Vancouver``)."""
    return _role_timezone_name('local')


def zone(tz_name: str) -> ZoneInfo:
    """``ZoneInfo`` for a registered IANA name."""
    if tz_name not in registered_timezone_names():
        msg = f'Unknown timezone {tz_name!r}; registry={sorted(registered_timezone_names())}'
        raise ValueError(msg)
    return ZoneInfo(tz_name)


def exchange_zone() -> ZoneInfo:
    """ZoneInfo for :func:`exchange_timezone_name`."""
    return zone(exchange_timezone_name())


def display_zone() -> ZoneInfo:
    """ZoneInfo for :func:`display_timezone_name`."""
    return zone(display_timezone_name())


def local_zone() -> ZoneInfo:
    """ZoneInfo for :func:`local_timezone_name`."""
    return zone(local_timezone_name())


def utc_offset_at(instant: datetime, tz_name: str) -> timedelta:
    """UTC offset at ``instant`` in ``tz_name`` (DST-aware; never use a fixed -08:00 table)."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    else:
        instant = instant.astimezone(UTC)
    local = instant.astimezone(zone(tz_name))
    offset = local.utcoffset()
    if offset is None:
        msg = f'No UTC offset for {tz_name!r} at {instant!r}'
        raise ValueError(msg)
    return offset


def utc_offset_seconds_at(instant: datetime, tz_name: str) -> int:
    """Signed offset from UTC in seconds at ``instant``."""
    return int(utc_offset_at(instant, tz_name).total_seconds())


def timezone_abbreviation(session_date: date, clock_time: time, tz_name: str) -> str:
    """Local abbreviation at a wall time (e.g. ``PDT``, ``EST``)."""
    dt = datetime.combine(session_date, clock_time, tzinfo=zone(tz_name))
    return dt.strftime('%Z')


def timezone_display_label(session_date: date, clock_time: time, tz_name: str) -> str:
    """Human label for CLI headers: ``America/Los_Angeles (PDT)``."""
    return f'{tz_name} ({timezone_abbreviation(session_date, clock_time, tz_name)})'


def timestamp_utc_series_to_zone(timestamp_utc: 'pd.Series', tz_name: str) -> 'pd.Series':
    """Convert UTC bar ``timestamp`` column to ``tz_name``."""
    return pd.to_datetime(timestamp_utc, utc=True).dt.tz_convert(zone(tz_name))
