"""Market app: symbols and historical OHLCV (market_security)."""
from django.apps import AppConfig


class MarketConfig(AppConfig):
    """Configuration for market data (Alpaca-fed bars, jambot-shaped schema)."""

    name = 'smbweb.apps.market'
    verbose_name = 'Market'
