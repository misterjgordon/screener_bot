"""
Django management command to import execution data from CSV or Excel files.

Usage:
    python smbweb/manage.py import_executions <file_path>

Examples:
    # Import from CSV file
    python smbweb/manage.py import_executions smb_trader_executions/executions_2026-01-23.csv

    # Import from Excel file
    python smbweb/manage.py import_executions executions_2026-01-23.xlsx

    # Import with verbose output
    python smbweb/manage.py import_executions executions_2026-01-23.csv --verbose
"""
import argparse
import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from smbweb.apps.executions.models import Execution


class Command(BaseCommand):
    help = 'Import execution data from CSV or Excel file into the database'

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            'file_path',
            type=str,
            help='Path to the CSV or Excel file to import'
        )
        parser.add_argument(
            '--skip-duplicates',
            action='store_true',
            help='Skip rows that already exist (based on timestamp, trader, symbol, change_type)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be imported without actually importing',
        )

    def handle(self, *args, **options):
        file_path = options['file_path']
        skip_duplicates = options['skip_duplicates']
        dry_run = options['dry_run']

        # Validate file exists
        if not Path(file_path).exists():
            raise CommandError(f'File not found: {file_path}')

        # Determine file type and read data
        file_ext = Path(file_path).suffix.lower()
        
        if file_ext == '.csv':
            rows = self._read_csv(file_path)
        elif file_ext in ['.xlsx', '.xls']:
            rows = self._read_excel(file_path)
        else:
            raise CommandError(
                f'Unsupported file type: {file_ext}. '
                'Supported formats: .csv, .xlsx, .xls'
            )

        if not rows:
            raise CommandError('No data found in file')

        self.stdout.write(
            self.style.SUCCESS(f'Found {len(rows)} rows to import')  # type: ignore[attr-defined]
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - No data will be imported')  # type: ignore[attr-defined]
            )
            self._show_preview(rows)
            return

        # Import data
        imported_count = 0
        skipped_count = 0
        error_count = 0
        errors = []

        with transaction.atomic():
            for i, row_data in enumerate(rows, 1):
                try:
                    # Check for duplicates if requested
                    if skip_duplicates and self._is_duplicate(row_data):
                        skipped_count += 1
                        continue

                    # Create Execution object
                    execution = self._create_execution(row_data)
                    execution.save()
                    imported_count += 1

                    if (i % 10 == 0) or (i == len(rows)):
                        self.stdout.write(
                            f'Processed {i}/{len(rows)} rows... '
                            f'({imported_count} imported, {skipped_count} skipped, {error_count} errors)',
                            ending='\r'
                        )

                except Exception as e:
                    error_count += 1
                    error_msg = f'Row {i}: {str(e)}'
                    errors.append(error_msg)
                    self.stdout.write(
                        self.style.ERROR(f'\n{error_msg}')  # type: ignore[attr-defined]
                    )

        # Summary
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(
            self.style.SUCCESS(  # type: ignore[attr-defined]
                f'Import complete!\n'
                f'  Imported: {imported_count}\n'
                f'  Skipped: {skipped_count}\n'
                f'  Errors: {error_count}'
            )
        )

        if errors:
            self.stdout.write('\nErrors encountered:')
            for error in errors[:10]:  # Show first 10 errors
                self.stdout.write(self.style.ERROR(f'  - {error}'))  # type: ignore[attr-defined]
            if len(errors) > 10:
                self.stdout.write(
                    self.style.WARNING(f'  ... and {len(errors) - 10} more errors')  # type: ignore[attr-defined]
                )

    def _read_csv(self, file_path: str) -> list[dict[str, object]]:
        """Read CSV file and return list of dictionaries."""
        rows: list[dict[str, object]] = []
        with Path(file_path).open(encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(cast('dict[str, object]', row))
        return rows

    def _read_excel(self, file_path: str) -> list[dict[str, object]]:
        """Read Excel file and return list of dictionaries."""
        try:
            df = pd.read_excel(file_path)
            return df.to_dict('records')
        except ImportError as e:
            raise CommandError(
                'Excel support requires openpyxl. Install it with: '
                'pip install openpyxl (or uv add openpyxl)'
            ) from e
        except Exception as e:
            raise CommandError(f'Error reading Excel file: {str(e)}') from e

    def _parse_timestamp(self, timestamp_str: object) -> datetime | None:
        """
        Parse timestamp string to datetime object.
        
        CSV timestamps are generated by format_timestamp() which uses datetime.now()
        (local time, PST in Vancouver). They may or may not have timezone info.
        
        To fix the date display issue, we need to explicitly mark these as PST timezone
        so PostgreSQL stores and displays them correctly.
        
        Handles formats:
        - ISO format: 2026-01-23T06:09:08 (treated as PST)
        - ISO with timezone: 2026-01-23T06:09:08 PST (PST suffix stripped, treated as PST)
        - Space format: 2026-01-23 06:09:08 (treated as PST)
        - Date only: 2026-01-23 (assumes 00:00:00 PST)
        """
        if timestamp_str is None:
            return None
        timestamp_str = str(timestamp_str).strip()
        if timestamp_str == '':
            return None
        
        # Remove common timezone suffixes if present (PST, PDT, UTC, etc.)
        # This handles cases where the CSV has been edited to include timezone info
        timezone_suffixes = [' PST', ' PDT', ' UTC', ' EST', ' EDT', ' CST', ' CDT', ' MST', ' MDT']
        for suffix in timezone_suffixes:
            if timestamp_str.endswith(suffix):
                timestamp_str = timestamp_str[:-len(suffix)].strip()
                break

        # Parse as PST time - CSV timestamps are from datetime.now() which is local time (PST)
        pst_tz = ZoneInfo('America/Vancouver')
        
        for fmt in [
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%S.%f',
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M:%S.%f',
            '%Y-%m-%d',
        ]:
            try:
                dt = datetime.strptime(timestamp_str, fmt)
                # Localize to PST timezone, then convert to naive UTC datetime
                # Django with USE_TZ=False will store this, and PostgreSQL will interpret correctly
                dt_pst = dt.replace(tzinfo=pst_tz)
                # Convert to UTC, then make naive (for USE_TZ=False)
                # This ensures PostgreSQL stores the correct UTC time
                dt_utc = dt_pst.astimezone(datetime.UTC)  # type: ignore[attr-defined]
                return dt_utc.replace(tzinfo=None)
            except ValueError:
                continue

        raise ValueError(f'Unable to parse timestamp: {timestamp_str}')

    def _parse_decimal(self, value: object) -> Decimal | None:
        """Parse value to Decimal, returning None if empty or invalid."""
        if value is None or value == '' or str(value).strip() == '':
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None

    def _parse_float(self, value: object) -> float | None:
        """Parse value to float, returning None if empty or invalid."""
        if value is None or value == '' or str(value).strip() == '':
            return None
        try:
            return float(str(value))
        except (ValueError, TypeError):
            return None

    def _parse_int(self, value: object) -> int | None:
        """Parse value to int, returning None if empty or invalid."""
        if value is None or value == '' or str(value).strip() == '':
            return None
        try:
            return int(float(str(value)))
        except (ValueError, TypeError):
            return None

    def _create_execution(self, row_data: dict[str, object]) -> Execution:
        """Create Execution object from row data dictionary."""
        # Parse timestamp
        timestamp = self._parse_timestamp(row_data.get('timestamp'))
        if timestamp is None:
            raise ValueError('Missing or invalid timestamp')

        # Required fields
        trader = str(row_data.get('trader', '')).strip()
        symbol = str(row_data.get('symbol', '')).strip()
        change_type = str(row_data.get('change_type', '')).strip()
        net_side = str(row_data.get('net_side', '')).strip()
        delta_magnitude = self._parse_float(row_data.get('delta_magnitude'))

        # Validate required fields
        if not trader:
            raise ValueError('Missing trader')
        if not symbol:
            raise ValueError('Missing symbol')
        if not change_type:
            raise ValueError('Missing change_type')
        if not net_side:
            raise ValueError('Missing net_side')
        if delta_magnitude is None:
            raise ValueError('Missing or invalid delta_magnitude')

        # Validate choices
        valid_change_types = [choice[0] for choice in Execution.CHANGE_TYPE_CHOICES]
        if change_type not in valid_change_types:
            raise ValueError(
                f'Invalid change_type: {change_type}. '
                f'Valid options: {", ".join(valid_change_types)}'
            )

        valid_sides = [choice[0] for choice in Execution.SIDE_CHOICES]
        if net_side not in valid_sides:
            raise ValueError(
                f'Invalid net_side: {net_side}. '
                f'Valid options: {", ".join(valid_sides)}'
            )

        # Optional fields
        entry_price = self._parse_decimal(row_data.get('entry_price'))
        stop_price = self._parse_decimal(row_data.get('stop_price'))
        take_profit_price = self._parse_decimal(row_data.get('take_profit_price'))
        order_id = str(row_data.get('order_id', '')).strip() or None
        shares = self._parse_int(row_data.get('shares'))
        total_risk = self._parse_float(row_data.get('total_risk'))
        risk_per_share = self._parse_float(row_data.get('risk_per_share'))
        market_value = self._parse_float(row_data.get('market_value'))

        return Execution(
            timestamp=timestamp,
            trader=trader,
            symbol=symbol,
            change_type=change_type,
            net_side=net_side,
            delta_magnitude=delta_magnitude,
            entry_price=entry_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            order_id=order_id,
            shares=shares,
            total_risk=total_risk,
            risk_per_share=risk_per_share,
            market_value=market_value,
        )

    def _is_duplicate(self, row_data: dict[str, object]) -> bool:
        """Check if a row already exists in the database."""
        timestamp = self._parse_timestamp(row_data.get('timestamp'))
        trader = str(row_data.get('trader', '')).strip()
        symbol = str(row_data.get('symbol', '')).strip()
        change_type = str(row_data.get('change_type', '')).strip()

        if not all([timestamp, trader, symbol, change_type]):
            return False

        return Execution.objects.filter(  # type: ignore[attr-defined]
            timestamp=timestamp,
            trader=trader,
            symbol=symbol,
            change_type=change_type,
        ).exists()

    def _show_preview(self, rows: list[dict[str, object]]) -> None:
        """Show preview of data that would be imported."""
        self.stdout.write('\nPreview of first 5 rows:')
        self.stdout.write('-' * 60)
        for i, row in enumerate(rows[:5], 1):
            self.stdout.write(f'\nRow {i}:')
            for key, value in row.items():
                self.stdout.write(f'  {key}: {value}')
