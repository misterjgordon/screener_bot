"""Tests for trading.execution_db and trading.record_executions (CSV timestamp helpers).

- **Mocked** (`TestSaveExecutionToDbMocked`): replaces `Execution.objects` with fakes. Nothing is
  written to PostgreSQL; failures show up as assertion errors on the mock (e.g. wrong kwargs to
  `create`).
- **Live DB** (`TestSaveExecutionToDbPostgres`): always uses database ``test_database_smb`` for
  inserts (override with ``EXECUTIONS_TEST_DB_NAME``). ``setUpClass`` switches
  ``DATABASES['default']['NAME']`` and reconnects so ``uv run python smbweb/manage.py test …`` works
  without exporting ``DB_NAME``. These tests use ``unittest.TestCase`` (not ``django.test.TestCase``)
  so Django does not create a cloned ``test_*`` database. Rows are deleted in ``setUp`` for the
  snapshot constants; they land in table ``executions`` on that DB.
- **CSV / Pacific wall** (`TestFormatTimestamp`, `TestGetExecutionsFilename`): `format_timestamp` and
  daily CSV filename align with `_parse_timestamp` / imports.

``django.setup()`` runs below before Django model imports so ``unittest`` can load this module.

Run (Postgres tests target ``test_database_smb`` automatically):

    uv run python -m unittest tests.test_execution_db
    uv run python smbweb/manage.py test tests.test_execution_db

Seed **committed** sample execution row (for TablePlus; uses current ``DATABASES`` / ``DB_NAME``):

    DB_NAME=test_database_smb uv run python -m tests.test_execution_db --seed-test-db

Add ``--force`` to insert again if duplicate key would skip. Uses same constants as below.
"""

import os
import sys
import unittest
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock
from unittest.mock import patch
from zoneinfo import ZoneInfo

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smbweb.settings')
django.setup()

from django.conf import settings  # noqa: E402
from django.db import connections  # noqa: E402

from smbweb.apps.executions.models import Execution  # noqa: E402
from trading.execution_db import _parse_timestamp  # noqa: E402
from trading.execution_db import save_execution_to_db  # noqa: E402
from trading.record_executions import format_timestamp  # noqa: E402
from trading.record_executions import get_executions_filename  # noqa: E402

# --- Snapshot-style inputs: NEW long equity in HIMS (as if from screener JSON) ---
TRADER = 'Justin Spero'
SYMBOL = 'UGRO'
CHANGE_TYPE = 'NEW'
NET_SIDE = 'long'
DELTA_MAGNITUDE = 10.0
ENTRY_PRICE = 33.20
STOP_PRICE = 32.20
TAKE_PROFIT_PRICE = 34.60
ORDER_ID = '1234567'
FILLED_PRICE = 33.18
SHARES = 100
TOTAL_RISK = 100.0
RISK_PER_SHARE = 1.0
RISK_PERCENT = 0.12
# Wall clock in America/Vancouver (matches format_timestamp + import_executions interpretation)
SNAPSHOT_DATE = '2026-03-25'
EXECUTION_TIME_LOCAL = '06:31:00'
TIMESTAMP_STR = f'{SNAPSHOT_DATE} {EXECUTION_TIME_LOCAL}'
# Live Postgres tests (TestSaveExecutionToDbPostgres) switch settings to this DB name.
EXECUTIONS_TEST_DB_NAME = os.environ.get('EXECUTIONS_TEST_DB_NAME', 'test_database_smb')


def _expected_utc_naive_for_vancouver_wall_time(date_part: str, time_part: str) -> datetime:
    """Reference UTC-naive instant for a Vancouver local wall time on that calendar date."""
    local = datetime.strptime(f'{date_part} {time_part}', '%Y-%m-%d %H:%M:%S').replace(
        tzinfo=ZoneInfo('America/Vancouver'),
    )
    return local.astimezone(UTC).replace(tzinfo=None)


def seed_sample_execution_into_db(*, force_duplicate: bool = False) -> None:
    """Insert sample execution row via ``save_execution_to_db`` (committed; not a unittest)."""
    save_execution_to_db(
        trader=TRADER,
        symbol=SYMBOL,
        change_type=CHANGE_TYPE,
        net_side=NET_SIDE,
        delta_magnitude=DELTA_MAGNITUDE,
        entry_price=ENTRY_PRICE,
        stop_price=STOP_PRICE,
        take_profit_price=TAKE_PROFIT_PRICE,
        order_id=ORDER_ID,
        filled_price=FILLED_PRICE,
        timestamp=TIMESTAMP_STR,
        shares=SHARES,
        total_risk=TOTAL_RISK,
        risk_per_share=RISK_PER_SHARE,
        risk_percent=RISK_PERCENT,
        skip_duplicates=not force_duplicate,
    )


class TestExecutionDbParseTimestamp(unittest.TestCase):
    """Tests for Vancouver → UTC-naive parsing (aligned with import_executions)."""

    def test_parse_timestamp_with_sample_constants(self) -> None:
        expected = _expected_utc_naive_for_vancouver_wall_time(SNAPSHOT_DATE, EXECUTION_TIME_LOCAL)
        got = _parse_timestamp(TIMESTAMP_STR)
        self.assertEqual(got, expected)

    def test_parse_timestamp_empty_returns_none(self) -> None:
        self.assertIsNone(_parse_timestamp(''))
        self.assertIsNone(_parse_timestamp('   '))

    def test_parse_timestamp_invalid_string_returns_none(self) -> None:
        self.assertIsNone(_parse_timestamp('completely invalid'))
        self.assertIsNone(_parse_timestamp('2026-02-30 12:00:00'))


class TestFormatTimestamp(unittest.TestCase):
    """Pacific wall strings from format_timestamp (pair of _parse_timestamp)."""

    def test_aware_utc_converts_to_vancouver_wall_string(self) -> None:
        dt_utc = datetime(2026, 3, 25, 13, 33, 53, tzinfo=UTC)
        self.assertEqual(format_timestamp(dt_utc), '2026-03-25 06:33:53')

    def test_naive_treated_as_vancouver_wall(self) -> None:
        dt_naive = datetime(2026, 3, 25, 6, 33, 53)
        self.assertEqual(format_timestamp(dt_naive), '2026-03-25 06:33:53')

    def test_aware_vancouver_passthrough_wall_string(self) -> None:
        dt_local = datetime(2026, 3, 25, 6, 33, 53, tzinfo=ZoneInfo('America/Vancouver'))
        self.assertEqual(format_timestamp(dt_local), '2026-03-25 06:33:53')


class TestGetExecutionsFilename(unittest.TestCase):
    """Daily executions CSV path uses Vancouver calendar date."""

    @patch('trading.record_executions.vancouver_today')
    def test_filename_uses_vancouver_local_date(self, mock_today: MagicMock) -> None:
        mock_today.return_value = date(2026, 3, 25)
        path = get_executions_filename()
        self.assertIn('executions_2026-03-25.csv', path)


class TestSaveExecutionToDbMocked(unittest.TestCase):
    """save_execution_to_db with mocked ORM only (no database)."""

    @patch('trading.execution_db.Execution.objects')
    def test_save_invokes_create_with_sample_constants(self, mock_objects: MagicMock) -> None:
        mock_objects.filter.return_value.exists.return_value = False

        save_execution_to_db(
            trader=TRADER,
            symbol=SYMBOL,
            change_type=CHANGE_TYPE,
            net_side=NET_SIDE,
            delta_magnitude=DELTA_MAGNITUDE,
            entry_price=ENTRY_PRICE,
            stop_price=STOP_PRICE,
            take_profit_price=TAKE_PROFIT_PRICE,
            order_id=ORDER_ID,
            filled_price=FILLED_PRICE,
            timestamp=TIMESTAMP_STR,
            shares=SHARES,
            total_risk=TOTAL_RISK,
            risk_per_share=RISK_PER_SHARE,
            risk_percent=RISK_PERCENT,
        )

        mock_objects.create.assert_called_once()
        kwargs = mock_objects.create.call_args.kwargs
        self.assertEqual(kwargs['trader'], TRADER)
        self.assertEqual(kwargs['symbol'], SYMBOL)
        self.assertEqual(kwargs['change_type'], CHANGE_TYPE)
        self.assertEqual(kwargs['net_side'], NET_SIDE)
        self.assertAlmostEqual(kwargs['delta_magnitude'], DELTA_MAGNITUDE)
        self.assertEqual(kwargs['entry_price'], Decimal(str(ENTRY_PRICE)))
        self.assertEqual(kwargs['stop_price'], Decimal(str(STOP_PRICE)))
        self.assertEqual(kwargs['take_profit_price'], Decimal(str(TAKE_PROFIT_PRICE)))
        self.assertEqual(kwargs['order_id'], ORDER_ID)
        self.assertEqual(kwargs['filled_price'], Decimal(str(FILLED_PRICE)))
        self.assertEqual(kwargs['shares'], SHARES)
        self.assertEqual(kwargs['total_risk'], TOTAL_RISK)
        self.assertEqual(kwargs['risk_per_share'], RISK_PER_SHARE)
        self.assertEqual(kwargs['risk_percent'], RISK_PERCENT)
        self.assertAlmostEqual(kwargs['market_value'], SHARES * ENTRY_PRICE)

    @patch('trading.execution_db.Execution.objects')
    def test_save_rounds_money_fields_to_two_decimals(self, mock_objects: MagicMock) -> None:
        mock_objects.filter.return_value.exists.return_value = False

        save_execution_to_db(
            trader=TRADER,
            symbol=SYMBOL,
            change_type=CHANGE_TYPE,
            net_side=NET_SIDE,
            delta_magnitude=DELTA_MAGNITUDE,
            entry_price=33.333,
            stop_price=STOP_PRICE,
            take_profit_price=TAKE_PROFIT_PRICE,
            order_id=ORDER_ID,
            filled_price=FILLED_PRICE,
            timestamp=TIMESTAMP_STR,
            shares=SHARES,
            total_risk=100.006,
            risk_per_share=1.234567,
            risk_percent=0.123456,
        )

        kwargs = mock_objects.create.call_args.kwargs
        self.assertEqual(kwargs['total_risk'], 100.01)
        self.assertEqual(kwargs['risk_per_share'], 1.23)
        self.assertEqual(kwargs['risk_percent'], 0.12)
        self.assertEqual(kwargs['market_value'], 3333.30)
        self.assertEqual(
            kwargs['timestamp'],
            _expected_utc_naive_for_vancouver_wall_time(SNAPSHOT_DATE, EXECUTION_TIME_LOCAL),
        )

    @patch('trading.execution_db.Execution.objects')
    def test_save_skips_create_when_duplicate(self, mock_objects: MagicMock) -> None:
        mock_objects.filter.return_value.exists.return_value = True

        save_execution_to_db(
            trader=TRADER,
            symbol=SYMBOL,
            change_type=CHANGE_TYPE,
            net_side=NET_SIDE,
            delta_magnitude=DELTA_MAGNITUDE,
            entry_price=ENTRY_PRICE,
            stop_price=STOP_PRICE,
            take_profit_price=TAKE_PROFIT_PRICE,
            order_id=ORDER_ID,
            filled_price=FILLED_PRICE,
            timestamp=TIMESTAMP_STR,
            shares=SHARES,
            total_risk=TOTAL_RISK,
            risk_per_share=RISK_PER_SHARE,
            risk_percent=RISK_PERCENT,
        )

        mock_objects.create.assert_not_called()

    @patch('trading.execution_db.logger.warning')
    @patch('trading.execution_db.Execution.objects')
    @patch('trading.execution_db._parse_timestamp')
    def test_save_when_parse_returns_none_does_not_touch_orm(
        self,
        mock_parse: MagicMock,
        mock_objects: MagicMock,
        mock_warning: MagicMock,
    ) -> None:
        """Guard path: no ORM when parse returns None; real invalid strings covered above."""
        mock_parse.return_value = None
        save_execution_to_db(
            trader=TRADER,
            symbol=SYMBOL,
            change_type=CHANGE_TYPE,
            net_side=NET_SIDE,
            delta_magnitude=DELTA_MAGNITUDE,
            timestamp=TIMESTAMP_STR,
        )
        mock_objects.filter.assert_not_called()
        mock_objects.create.assert_not_called()
        mock_warning.assert_called_once()


class TestSaveExecutionToDbPostgres(unittest.TestCase):
    """Real INSERT via ``save_execution_to_db`` into ``EXECUTIONS_TEST_DB_NAME`` (default ``test_database_smb``)."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._saved_db_name = settings.DATABASES['default']['NAME']
        if cls._saved_db_name != EXECUTIONS_TEST_DB_NAME:
            settings.DATABASES['default']['NAME'] = EXECUTIONS_TEST_DB_NAME
            connections.close_all()

    @classmethod
    def tearDownClass(cls) -> None:
        if settings.DATABASES['default']['NAME'] != cls._saved_db_name:
            settings.DATABASES['default']['NAME'] = cls._saved_db_name
            connections.close_all()
        super().tearDownClass()

    def setUp(self) -> None:
        ts = _expected_utc_naive_for_vancouver_wall_time(SNAPSHOT_DATE, EXECUTION_TIME_LOCAL)
        Execution.objects.filter(  # type: ignore[attr-defined]
            timestamp=ts,
            trader=TRADER,
            symbol=SYMBOL,
            change_type=CHANGE_TYPE,
        ).delete()

    def test_insert_then_read_sample_row(self) -> None:
        expected_ts = _expected_utc_naive_for_vancouver_wall_time(SNAPSHOT_DATE, EXECUTION_TIME_LOCAL)

        save_execution_to_db(
            trader=TRADER,
            symbol=SYMBOL,
            change_type=CHANGE_TYPE,
            net_side=NET_SIDE,
            delta_magnitude=DELTA_MAGNITUDE,
            entry_price=ENTRY_PRICE,
            stop_price=STOP_PRICE,
            take_profit_price=TAKE_PROFIT_PRICE,
            order_id=ORDER_ID,
            filled_price=FILLED_PRICE,
            timestamp=TIMESTAMP_STR,
            shares=SHARES,
            total_risk=TOTAL_RISK,
            risk_per_share=RISK_PER_SHARE,
            risk_percent=RISK_PERCENT,
        )

        row = Execution.objects.get(  # type: ignore[attr-defined]
            timestamp=expected_ts,
            trader=TRADER,
            symbol=SYMBOL,
            change_type=CHANGE_TYPE,
        )
        self.assertEqual(row.net_side, NET_SIDE)
        self.assertAlmostEqual(row.delta_magnitude, DELTA_MAGNITUDE)
        self.assertEqual(row.entry_price, Decimal(str(ENTRY_PRICE)))
        self.assertEqual(row.stop_price, Decimal(str(STOP_PRICE)))
        self.assertEqual(row.take_profit_price, Decimal(str(TAKE_PROFIT_PRICE)))
        self.assertEqual(row.order_id, ORDER_ID)
        self.assertEqual(row.filled_price, Decimal(str(FILLED_PRICE)))
        self.assertEqual(row.shares, SHARES)
        self.assertEqual(row.total_risk, TOTAL_RISK)
        self.assertEqual(row.risk_per_share, RISK_PER_SHARE)
        self.assertEqual(row.risk_percent, RISK_PERCENT)
        self.assertAlmostEqual(row.market_value, SHARES * ENTRY_PRICE)

    def test_second_identical_save_skipped_as_duplicate(self) -> None:
        for _ in range(2):
            save_execution_to_db(
                trader=TRADER,
                symbol=SYMBOL,
                change_type=CHANGE_TYPE,
                net_side=NET_SIDE,
                delta_magnitude=DELTA_MAGNITUDE,
                entry_price=ENTRY_PRICE,
                stop_price=STOP_PRICE,
                take_profit_price=TAKE_PROFIT_PRICE,
                order_id=ORDER_ID,
                filled_price=FILLED_PRICE,
                timestamp=TIMESTAMP_STR,
                shares=SHARES,
                total_risk=TOTAL_RISK,
                risk_per_share=RISK_PER_SHARE,
                risk_percent=RISK_PERCENT,
            )
        expected_ts = _expected_utc_naive_for_vancouver_wall_time(SNAPSHOT_DATE, EXECUTION_TIME_LOCAL)
        n = Execution.objects.filter(  # type: ignore[attr-defined]
            timestamp=expected_ts,
            trader=TRADER,
            symbol=SYMBOL,
            change_type=CHANGE_TYPE,
        ).count()
        self.assertEqual(n, 1)


if __name__ == '__main__':
    if '--seed-test-db' in sys.argv:
        force = '--force' in sys.argv
        seed_sample_execution_into_db(force_duplicate=force)
        db_name = settings.DATABASES['default']['NAME']
        print(
            f'Seeded {TRADER} {SYMBOL} {CHANGE_TYPE} @ {TIMESTAMP_STR} '
            f'into database {db_name!r} (table executions)'
        )
        sys.exit(0)
    unittest.main(buffer=False)
