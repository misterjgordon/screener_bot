"""Session extension indicators: how far price has moved from RTH open in ATR units."""

import pandas as pd


def _rth_open_by_date(open: pd.Series, session: pd.Series, trading_date: pd.Series) -> pd.Series:
    """Open of the first RTH bar per trading_date, broadcast to all bars."""
    df = pd.DataFrame({'open': open, 'session': session, 'trading_date': trading_date})
    rth_first_open = (
        df[df['session'] == 'RTH']
        .groupby('trading_date')['open']
        .first()
    )
    return trading_date.map(rth_first_open)


def extension_long_series(
    open: pd.Series,
    session: pd.Series,
    trading_date: pd.Series,
    lod: pd.Series,
    atr: pd.Series,
) -> pd.Series:
    """Session down-from-open extension in ATR units: (rth_open - lod) / atr.

    Positive values mean the session has sold off from the open. NaN on PM and AH bars
    (lod is NaN there) and when atr is zero or NaN.

    Parameters
    ----------
    open:
        Bar open prices (used to find RTH session open per day).
    session:
        Session label per bar (PM/RTH/AH).
    trading_date:
        ET calendar date per bar.
    lod:
        Cumulative RTH low of day (from the lod indicator).
    atr:
        Daily ATR (from the atr indicator).
    """
    rth_open = _rth_open_by_date(open, session, trading_date)
    return (rth_open - lod) / atr


def extension_short_series(
    open: pd.Series,
    session: pd.Series,
    trading_date: pd.Series,
    hod: pd.Series,
    atr: pd.Series,
) -> pd.Series:
    """Session up-from-open extension in ATR units: (hod - rth_open) / atr.

    Positive values mean the session has rallied from the open. NaN on PM and AH bars
    (hod is NaN there) and when atr is zero or NaN.

    Parameters
    ----------
    open:
        Bar open prices (used to find RTH session open per day).
    session:
        Session label per bar (PM/RTH/AH).
    trading_date:
        ET calendar date per bar.
    hod:
        Cumulative RTH high of day (from the hod indicator).
    atr:
        Daily ATR (from the atr indicator).
    """
    rth_open = _rth_open_by_date(open, session, trading_date)
    return (hod - rth_open) / atr
