"""Market timezone config and DST-safe offset helpers."""

from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import time

import pytest

from trading.market_timezones import display_timezone_name
from trading.market_timezones import display_zone
from trading.market_timezones import exchange_timezone_name
from trading.market_timezones import exchange_zone
from trading.market_timezones import local_timezone_name
from trading.market_timezones import local_zone
from trading.market_timezones import registered_timezone_names
from trading.market_timezones import timezone_display_label
from trading.market_timezones import utc_offset_seconds_at
from trading.market_timezones import zone

UTC = UTC


def test_registered_timezones_include_exchange_and_display() -> None:
    names = registered_timezone_names()
    assert 'UTC' in names
    assert 'America/New_York' in names
    assert 'America/Los_Angeles' in names


def test_default_role_names_from_yaml() -> None:
    assert exchange_timezone_name() == 'America/New_York'
    assert display_timezone_name() == 'America/Los_Angeles'
    assert local_timezone_name() == 'America/Vancouver'


def test_exchange_and_display_zones_match_names() -> None:
    assert exchange_zone().key == exchange_timezone_name()
    assert display_zone().key == display_timezone_name()
    assert local_zone().key == local_timezone_name()


def test_utc_offset_seconds_pdt_vs_est() -> None:
    summer = datetime(2026, 5, 15, 16, 0, tzinfo=UTC)
    winter = datetime(2026, 1, 15, 16, 0, tzinfo=UTC)
    la_summer = utc_offset_seconds_at(summer, 'America/Los_Angeles')
    la_winter = utc_offset_seconds_at(winter, 'America/Los_Angeles')
    ny_summer = utc_offset_seconds_at(summer, 'America/New_York')
    assert la_summer == -7 * 3600
    assert la_winter == -8 * 3600
    assert ny_summer == -4 * 3600


def test_timezone_display_label() -> None:
    label = timezone_display_label(date(2026, 5, 15), time(9, 0), display_timezone_name())
    assert label == 'America/Los_Angeles (PDT)'


def test_zone_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match='Unknown timezone'):
        zone('Europe/Paris')
