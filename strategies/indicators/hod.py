"""High of day: cumulative RTH high from session open through each bar."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def hod_series(high: 'pd.Series', session: 'pd.Series', trading_date: 'pd.Series') -> 'pd.Series':
    """Cumulative RTH high of day through each bar. NaN on PM and AH bars.

    Parameters
    ----------
    high:
        Bar high prices.
    session:
        Session label per bar (PM/RTH/AH) from the indicator pipeline.
    trading_date:
        ET calendar date per bar.
    """
    rth_high = high.where(session == 'RTH')
    return rth_high.groupby(trading_date).transform('cummax')
