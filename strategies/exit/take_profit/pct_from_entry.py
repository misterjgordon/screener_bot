"""Take profit as a fraction above entry fill (long)."""

INDICATOR_DECIMAL_PLACES = 2


def take_profit_price_long(entry_price: float, pct: float) -> float:
    """Long target: ``pct`` fraction above entry fill."""
    return round(entry_price * (1.0 + pct), INDICATOR_DECIMAL_PLACES)
