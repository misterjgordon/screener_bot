"""Listing venues, equity master rows, and OHLCV bars (market_bars)."""
import logging
from datetime import UTC
from datetime import datetime as dt
from typing import TYPE_CHECKING
from typing import Self
from typing import cast
from typing import override

import pandas as pd
import sqlalchemy as sa
from django.db import models
from django.db.models import CompositePrimaryKey
from django.db.models import Max
from django.db.models import PositiveSmallIntegerField
from django.db.models import Q
from django.db.models import Value
from django.db.models.functions import Coalesce

from smbweb.apps.market.bar_time import floor_now_utc_to_interval_minutes
from smbweb.dbconn import db
from trading.integrations.alpaca_bars import DEFAULT_BAR_SIZE_MINUTES
from trading.integrations.alpaca_bars import fetch_stock_bars_dataframe

if TYPE_CHECKING:
    from pandas._typing import DtypeArg

log = logging.getLogger(__name__)


class SymbolQuerySet(models.QuerySet):
    default_date = dt(2026, 1, 1)
    default_bar_columns = [
        'symbol',
        'interval',
        'timestamp',
        'open',
        'high',
        'low',
        'close',
        'volume',
    ]

    def active(self) -> Self:
        return self.filter(is_active=True)

    def annotate_max_dates(self, interval: int) -> Self:
        return self.annotate(
            max_date=Coalesce(
                Max('bars__timestamp', filter=Q(bars__interval=interval)),
                Value(self.default_date),
            ),
        )

    def get_new_bar_data(
            self,
            interval: int = DEFAULT_BAR_SIZE_MINUTES,
            start: dt | None = None,
            end: dt | None = None) -> pd.DataFrame:
        symbols = list(self.annotate_max_dates(interval))
        end_utc = end or floor_now_utc_to_interval_minutes(interval)
        if end_utc.tzinfo is None:
            end_utc = end_utc.replace(tzinfo=UTC)
        else:
            end_utc = end_utc.astimezone(UTC)

        dfs: list[pd.DataFrame] = []

        for sym in symbols:
            start_raw = start or sym.max_date
            start_utc = (
                start_raw.replace(tzinfo=UTC)
                if start_raw.tzinfo is None
                else start_raw.astimezone(UTC)
            )

            if start_utc >= end_utc:
                log.warning('start >= end, skipping %s', sym.symbol)
                continue

            log.info('%s: fetching %s to %s', sym.symbol, start_utc, end_utc)

            df_fetch = fetch_stock_bars_dataframe(
                symbols=[sym.symbol],
                start=start_utc,
                end=end_utc,
                bar_size_minutes=interval,
            )

            if len(df_fetch) == 0:
                continue

            if df_fetch.timestamp.dt.tz is not None:
                df_fetch = df_fetch[df_fetch.timestamp > start_utc]
            else:
                df_fetch = df_fetch[df_fetch.timestamp > start_raw.replace(tzinfo=None)]

            df_fetch = df_fetch[~df_fetch.duplicated(subset=['symbol', 'timestamp'])]
            dfs.append(df_fetch)

        if not dfs:
            return pd.DataFrame.from_dict({c: [] for c in self.default_bar_columns})

        return pd.concat(dfs) \
            .sort_values(['symbol', 'timestamp']) \
            .reset_index(drop=True)

    def update_from_data_source(
            self,
            interval: int = DEFAULT_BAR_SIZE_MINUTES,
            start: dt | None = None,
            end: dt | None = None) -> pd.DataFrame:
        df = self.get_new_bar_data(interval=interval, start=start, end=end)

        if len(df) == 0:
            log.info('market_bars: no new rows')
            return df

        # Keep UTC-aware timestamps so Postgres timestamptz stores the true instant (naive values
        # are interpreted in the session timezone and mis-label ET bars as local wall time).
        if df.timestamp.dt.tz is None:
            df = df.assign(timestamp=pd.to_datetime(df.timestamp, utc=True))
        else:
            df = df.assign(timestamp=df.timestamp.dt.tz_convert('UTC'))

        tickers = df.symbol.unique()
        log.info('market_bars: importing %s rows for %s', len(df), tickers)

        df.to_sql(
            'market_bars',
            con=db.engine,
            if_exists='append',
            chunksize=5000,
            index=False,
            dtype=cast(
                'DtypeArg',
                {'timestamp': sa.DateTime(timezone=True)},
            ),
        )

        return df


SymbolManager = models.Manager.from_queryset(SymbolQuerySet)


class Symbol(models.Model):
    """Master list of equities: ticker, listing venue, optional company legal name.

    Does not define bar sizes or execution mechanics; those live on ingest and trading code.
    Primary key is ``symbol`` (ticker) alone — unique without encoding venue in the key.
    """

    class Meta:
        db_table = 'market_symbol'

    objects = SymbolManager()

    symbol = models.CharField(max_length=20, primary_key=True)
    exchange = models.CharField(max_length=20)
    company_name = models.CharField(max_length=200, blank=True, default='')
    is_active = models.BooleanField(default=True)  # pyright: ignore[reportArgumentType]

    @override
    def save(self, *args, **kw) -> None:
        self.symbol = str(self.symbol).strip().upper()
        super().save(*args, **kw)

    def __str__(self) -> str:
        return str(self.pk)

    @classmethod
    def update_all_active_from_data_source(cls, interval: int = DEFAULT_BAR_SIZE_MINUTES) -> None:
        cls.objects.active().update_from_data_source(interval=interval)


class Bars(models.Model):
    """OHLCV bar row; table ``market_bars``. Listing venue is not stored here — only FK to Symbol."""

    pk = CompositePrimaryKey('interval', 'symbol', 'timestamp')
    interval = PositiveSmallIntegerField()
    symbol = models.ForeignKey(
        Symbol,
        to_field='symbol',
        db_column='symbol',
        on_delete=models.CASCADE,
        related_name='bars',
    )
    timestamp = models.DateTimeField()
    open = models.DecimalField(max_digits=16, decimal_places=2)
    high = models.DecimalField(max_digits=16, decimal_places=2)
    low = models.DecimalField(max_digits=16, decimal_places=2)
    close = models.DecimalField(max_digits=16, decimal_places=2)
    volume = models.DecimalField(max_digits=16, decimal_places=0)

    class Meta:
        db_table = 'market_bars'

    def __str__(self) -> str:
        return f'{self.symbol} {self.interval} {self.timestamp}'
