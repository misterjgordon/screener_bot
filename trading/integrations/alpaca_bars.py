"""Alpaca Market Data API: fetch equity bars and normalize to a DataFrame.

Uses alpaca-py against the Data API host (ALPACA_DATA_BASE_URL), not the paper
trading API. Django-free so smbweb and scripts can share the same fetch path.

Session scope: the free IEX feed is effectively regular-hours (RTH) equity data.
Extended / all-session bars require a paid feed (e.g. SIP); smbweb can switch
feed and windows when that is enabled.
"""

from datetime import UTC
from datetime import datetime
from typing import cast

import pandas as pd
from alpaca.data.enums import Adjustment
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.models import BarSet
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.timeframe import TimeFrameUnit

from trading import config as cf
from trading.storage.ohlcv.ohlcv_schema import BAR_FRAME_COLUMNS
from trading.storage.ohlcv.ohlcv_schema import empty_bars_dataframe

# Default bar size for SMB mirror (1-minute bars; was 15 in jambot crypto).
DEFAULT_BAR_SIZE_MINUTES = 1


def _require_alpaca_keys() -> tuple[str, str]:
    """Return API credentials or raise; never returns empty strings.

    Alpaca's StockHistoricalDataClient allows Optional keys; we require both
    so callers fail fast instead of sending unauthenticated requests.
    """
    api_key = cf.ALPACA_API_KEY.strip() if cf.ALPACA_API_KEY else ''
    secret_key = cf.ALPACA_SECRET_KEY.strip() if cf.ALPACA_SECRET_KEY else ''

    if not api_key or not secret_key:
        msg = 'Set non-empty ALPACA_API_KEY and ALPACA_SECRET_KEY in the environment'
        raise ValueError(msg)

    return api_key, secret_key


def _stock_historical_client() -> StockHistoricalDataClient:
    """Build a client for https://data.alpaca.markets (configurable via env)."""
    api_key, secret_key = _require_alpaca_keys()

    return StockHistoricalDataClient(
        api_key=api_key,
        secret_key=secret_key,
        url_override=cf.ALPACA_DATA_BASE_URL,
    )


def timeframe_for_bar_size(bar_size_minutes: int) -> TimeFrame:
    """Map bar size in minutes to an Alpaca TimeFrame (minute unit only for now)."""
    if bar_size_minutes < 1:
        raise ValueError('bar_size_minutes must be >= 1')

    unit = cast('TimeFrameUnit', TimeFrameUnit.Minute)
    return TimeFrame(bar_size_minutes, unit)


def _bar_timestamp_utc(timestamp: datetime) -> datetime:
    """Return bar open time as timezone-aware UTC."""
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)

    return timestamp.astimezone(UTC)


def bar_set_to_dataframe(
        bar_set: BarSet,
        *,
        bar_size_minutes: int,
) -> pd.DataFrame:
    """Turn a BarSet into a sorted DataFrame ready for DB ingest.

    Columns: symbol, interval, timestamp, open, high, low, close, volume, vwap.
    interval is bar length in minutes (matches jambot market_security.interval).
    Timestamps are UTC. symbol is the equity ticker (matches market_symbol.symbol).
    """
    rows: list[dict] = []

    for symbol, bars in bar_set.data.items():
        for bar in bars:
            rows.append(
                {
                    'symbol': symbol,
                    'interval': bar_size_minutes,
                    'timestamp': _bar_timestamp_utc(bar.timestamp),
                    'open': float(bar.open),
                    'high': float(bar.high),
                    'low': float(bar.low),
                    'close': float(bar.close),
                    'volume': float(bar.volume),
                    'vwap': float(bar.vwap) if bar.vwap is not None else None,
                }
            )

    if not rows:
        return empty_bars_dataframe()

    df = pd.DataFrame(rows)

    return df \
        .loc[:, BAR_FRAME_COLUMNS] \
        .sort_values(['symbol', 'timestamp']) \
        .reset_index(drop=True)


def fetch_stock_bars_dataframe(
        symbols: list[str],
        start: datetime,
        end: datetime,
        *,
        bar_size_minutes: int = DEFAULT_BAR_SIZE_MINUTES,
        adjustment: Adjustment = Adjustment.RAW,
        feed: DataFeed | None = DataFeed.IEX,
) -> pd.DataFrame:
    """Fetch historical stock bars from Alpaca and return a normalized DataFrame.

    Parameters
    ----------
    symbols
        Alpaca equity tickers (e.g. ['AAPL', 'MSFT']).
    start, end
        Query window; timezone-aware values are converted to UTC for the API.
    bar_size_minutes
        Bar length in minutes (default 1). Minute-based TimeFrame only.
    adjustment
        Corporate-action adjustment (default RAW, matching typical REST examples).
    feed
        Data feed (default IEX, no paid subscription; RTH-oriented). Use SIP
        when subscribed for fuller session coverage.

    Returns
    -------
    pd.DataFrame
        Columns: symbol, interval, timestamp, open, high, low, close, volume, vwap.
    """
    if not symbols:
        return bar_set_to_dataframe(BarSet({}), bar_size_minutes=bar_size_minutes)

    client = _stock_historical_client()
    timeframe = timeframe_for_bar_size(bar_size_minutes)

    request_kw: dict = {
        'symbol_or_symbols': symbols,
        'timeframe': timeframe,
        'start': start,
        'end': end,
        'adjustment': adjustment,
    }

    if feed is not None:
        request_kw['feed'] = feed

    request = StockBarsRequest(**request_kw)
    bar_set = client.get_stock_bars(request)

    if not isinstance(bar_set, BarSet):
        msg = f'Expected BarSet from get_stock_bars, got {type(bar_set)}'
        raise TypeError(msg)

    return bar_set_to_dataframe(bar_set, bar_size_minutes=bar_size_minutes)
