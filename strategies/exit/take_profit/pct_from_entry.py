"""Take profit as a fraction from entry fill (long and short)."""

INDICATOR_DECIMAL_PLACES = 2


def take_profit_price_long(entry_price: float, pct: float) -> float:
    """Long target: ``pct`` fraction above entry fill."""
    return round(entry_price * (1.0 + pct), INDICATOR_DECIMAL_PLACES)


def take_profit_price_short(entry_price: float, pct: float) -> float:
    """Short target: ``pct`` fraction below entry fill."""
    return round(entry_price * (1.0 - pct), INDICATOR_DECIMAL_PLACES)
