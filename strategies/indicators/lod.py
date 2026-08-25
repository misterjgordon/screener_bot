"""Low of day: cumulative RTH low from session open through each bar."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def lod_series(low: 'pd.Series', session: 'pd.Series', trading_date: 'pd.Series') -> 'pd.Series':
    """Cumulative RTH low of day through each bar. NaN on PM and AH bars.

    Parameters
    ----------
    low:
        Bar low prices.
    session:
        Session label per bar (PM/RTH/AH) from the indicator pipeline.
    trading_date:
        ET calendar date per bar.
    """
    rth_low = low.where(session == 'RTH')
    return rth_low.groupby(trading_date).transform('cummin')
