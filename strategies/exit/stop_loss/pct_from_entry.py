"""Stop loss as a fraction from entry fill (long and short)."""

INDICATOR_DECIMAL_PLACES = 2


def stop_loss_price_long(entry_price: float, pct: float) -> float:
    """Long stop: ``pct`` fraction below entry fill."""
    return round(entry_price * (1.0 - pct), INDICATOR_DECIMAL_PLACES)


def stop_loss_price_short(entry_price: float, pct: float) -> float:
    """Short stop: ``pct`` fraction above entry fill."""
    return round(entry_price * (1.0 + pct), INDICATOR_DECIMAL_PLACES)
