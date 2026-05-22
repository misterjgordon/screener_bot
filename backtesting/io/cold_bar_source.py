"""Read OHLCV from the cold Parquet lake with warmup and session-day anchoring."""

from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from backtesting.bt_config import DAILY_INTERVAL_MINUTES
from backtesting.bt_config import DEFAULT_INTERVAL_MINUTES
from backtesting.bt_config import DEFAULT_WARMUP_BARS
from backtesting.bt_config import PM_LOAD_CUSHION_HOURS
from backtesting.frames.bar_price_round import round_loaded_bar_prices
from backtesting.frames.symbol_bar_frame import SymbolBarFrame
from backtesting.indicators.indicator_catalog_load import daily_bar_lookback_calendar_days
from backtesting.indicators.indicator_catalog_load import history_bar_lookback_calendar_days
from backtesting.indicators.indicator_catalog_load import min_daily_sessions_for_indicators
from strategies.indicators.daily_rth import aggregate_rth_daily_from_intraday
from strategies.indicators.trading_date import trading_date_series_utc
from trading.market_timezones import exchange_zone
from trading.storage.ohlcv.ohlcv_paths import symbol_path
from trading.storage.ohlcv.ohlcv_prepare import validate_and_prepare
from trading.storage.ohlcv.ohlcv_schema import OHLCV_COLD_PARQUET_COLUMNS


def _analysis_bounds_utc(start: date, end: date) -> tuple[datetime, datetime]:
    """Inclusive ET calendar ``start``..``end`` as UTC half-open ``[lo, hi)``."""
    if end < start:
        msg = f'end date {end} is before start date {start}'
        raise ValueError(msg)
    et_zone = exchange_zone()
    lo_et = datetime.combine(start, time.min, tzinfo=et_zone)
    hi_et = datetime.combine(end + timedelta(days=1), time.min, tzinfo=et_zone)
    return lo_et.astimezone(UTC), hi_et.astimezone(UTC)


def _parquet_timestamp_filters(
    read_start_utc: datetime,
    read_end_utc_exclusive: datetime,
) -> list[tuple[str, str, pa.Scalar]]:
    ts_type = pa.timestamp('us', tz='UTC')
    return [
        ('timestamp', '>=', pa.scalar(read_start_utc, type=ts_type)),
        ('timestamp', '<', pa.scalar(read_end_utc_exclusive, type=ts_type)),
    ]


def _read_parquet_window(
    p_parquet: Path,
    read_start_utc: datetime,
    read_end_utc_exclusive: datetime,
) -> pd.DataFrame:
    if not p_parquet.is_file():
        msg = f'Cold Parquet not found: {p_parquet}'
        raise FileNotFoundError(msg)

    filters = _parquet_timestamp_filters(read_start_utc, read_end_utc_exclusive)
    try:
        table = pq.read_table(p_parquet, filters=filters, columns=list(OHLCV_COLD_PARQUET_COLUMNS))
    except (OSError, pa.ArrowInvalid):
        df_raw = pd.read_parquet(p_parquet, columns=list(OHLCV_COLD_PARQUET_COLUMNS))
        ts = pd.to_datetime(df_raw.timestamp, utc=True)
        mask = (ts >= read_start_utc) & (ts < read_end_utc_exclusive)
        return df_raw.loc[mask].copy()

    return table.to_pandas()


def _prepare_sorted(df_bars: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if df_bars.empty:
        return df_bars

    df_norm = round_loaded_bar_prices(validate_and_prepare(df_bars))
    df_norm = df_norm.sort_values('timestamp').drop_duplicates(
        subset=['symbol', 'timestamp'],
        keep='last',
    )
    sym_upper = symbol.strip().upper()
    if not (df_norm.symbol.astype(str).str.upper() == sym_upper).all():
        msg = f'Parquet rows are not all for symbol {sym_upper}'
        raise ValueError(msg)
    return df_norm.reset_index(drop=True)


def _session_days_in_analysis_window(
    df_bars: pd.DataFrame,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Keep full session days (PM through AH) for each ET ``trading_date`` in range."""
    if df_bars.empty:
        return df_bars

    session_dates = trading_date_series_utc(df_bars.timestamp)
    mask = (session_dates >= start) & (session_dates <= end)
    return df_bars.loc[mask].reset_index(drop=True)


class ColdBarSource:
    """Load per-symbol bars from cold Parquet for a calendar analysis window.

    Reads extra history for indicator warmup and PM anchoring on the first session
    day. ``load`` returns only bars whose ``trading_date`` (ET) falls in
    ``[start, end]``; warmup rows are available via ``warmup_bars`` for the
    feature pipeline.
    """

    def __init__(
        self,
        start: date,
        end: date,
        *,
        interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
        warmup_bars: int = DEFAULT_WARMUP_BARS,
    ) -> None:
        self._start = start
        self._end = end
        self._interval_minutes = interval_minutes
        self._warmup_bars = warmup_bars
        self._analysis_lo_utc, self._analysis_hi_utc = _analysis_bounds_utc(start, end)
        self._warmup_by_symbol: dict[str, pd.DataFrame] = {}

    @property
    def start(self) -> date:
        return self._start

    @property
    def end(self) -> date:
        return self._end

    @property
    def interval_minutes(self) -> int:
        return self._interval_minutes

    @property
    def warmup_bar_count(self) -> int:
        return self._warmup_bars

    def _read_start_utc(self) -> datetime:
        """Earliest 1m timestamp to load: PM cushion, EMA warmup, and RVOL session history."""
        cushion = timedelta(hours=PM_LOAD_CUSHION_HOURS)
        warmup = timedelta(minutes=self._warmup_bars * self._interval_minutes)
        history = timedelta(days=history_bar_lookback_calendar_days())
        return self._analysis_lo_utc - max(cushion + warmup, history)

    def _daily_read_start_utc(self) -> datetime:
        """Earlier bound than 1m warmup so ADR/ATR have enough RTH daily sessions."""
        lookback = timedelta(days=daily_bar_lookback_calendar_days())
        return self._analysis_lo_utc - lookback

    def _load_prepared_window(
        self,
        symbol: str,
        read_start_utc: datetime,
    ) -> pd.DataFrame:
        sym = symbol.strip().upper()
        p_parquet = symbol_path(sym, interval_minutes=self._interval_minutes)
        df_raw = _read_parquet_window(
            p_parquet,
            read_start_utc,
            self._analysis_hi_utc,
        )
        return _prepare_sorted(df_raw, sym)

    def _load_prepared(self, symbol: str) -> pd.DataFrame:
        return self._load_prepared_window(symbol, self._read_start_utc())

    def _aggregate_daily_from_intraday(self, symbol: str, read_start_utc: datetime) -> pd.DataFrame:
        df_intraday = self._load_prepared_window(symbol, read_start_utc)
        return aggregate_rth_daily_from_intraday(df_intraday)

    def _load_daily_bars(self, symbol: str) -> pd.DataFrame:
        """RTH daily OHLCV from ``1440m`` Parquet when present, else aggregate from intraday."""
        sym = symbol.strip().upper()
        read_start = self._daily_read_start_utc()
        min_sessions = min_daily_sessions_for_indicators()
        p_daily = symbol_path(sym, interval_minutes=DAILY_INTERVAL_MINUTES)
        if p_daily.is_file():
            df_raw = _read_parquet_window(
                p_daily,
                read_start,
                self._analysis_hi_utc,
            )
            if not df_raw.empty:
                df_norm = round_loaded_bar_prices(validate_and_prepare(df_raw))
                if 'trading_date' not in df_norm.columns:
                    df_norm = df_norm.assign(
                        trading_date=trading_date_series_utc(df_norm.timestamp),
                    )
                session_count = df_norm.trading_date.nunique()
                if session_count >= min_sessions:
                    return df_norm
        return self._aggregate_daily_from_intraday(sym, read_start)

    def load(self, symbol: str) -> SymbolBarFrame:
        """Load analysis-window bars for ``symbol`` (full session days in range)."""
        sym = symbol.strip().upper()
        df_all = self._load_prepared(sym)
        df_analysis = _session_days_in_analysis_window(df_all, self._start, self._end)

        df_daily = self._load_daily_bars(sym)

        if df_analysis.empty:
            self._warmup_by_symbol[sym] = df_analysis
            return SymbolBarFrame(
                symbol=sym,
                interval_minutes=self._interval_minutes,
                bars=df_analysis,
                daily_bars=df_daily,
                history_bars=df_all,
            )

        first_ts = df_analysis.timestamp.min()
        ts_utc = pd.to_datetime(df_all.timestamp, utc=True)
        df_prior = df_all.loc[ts_utc < first_ts]
        if self._warmup_bars > 0 and not df_prior.empty:
            self._warmup_by_symbol[sym] = df_prior.tail(self._warmup_bars).reset_index(drop=True)
        else:
            self._warmup_by_symbol[sym] = df_prior.iloc[0:0].reset_index(drop=True)

        return SymbolBarFrame(
            symbol=sym,
            interval_minutes=self._interval_minutes,
            bars=df_analysis,
            daily_bars=df_daily,
            history_bars=df_all,
        )

    def warmup_bars(self, symbol: str) -> pd.DataFrame:
        """Rows immediately before the analysis window (for indicator warmup)."""
        sym = symbol.strip().upper()
        if sym not in self._warmup_by_symbol:
            self.load(sym)
        return self._warmup_by_symbol[sym].copy()
