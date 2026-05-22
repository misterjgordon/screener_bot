"""Other exit: close on the wrong side of an EMA (level check per bar)."""

from typing import TYPE_CHECKING
from typing import Literal

if TYPE_CHECKING:
    import pandas as pd

ExitSide = Literal['long', 'short']


def close_past_ema_exit_series(
    close: 'pd.Series',
    ema: 'pd.Series',
    *,
    side: ExitSide,
) -> 'pd.Series':
    """True when price is past the EMA in the exit direction for ``side``.

    Long: exit when ``close < ema``. Short: exit when ``close > ema``.
    """
    close_f = close.astype('float64')
    ema_f = ema.astype('float64')
    if side == 'long':
        return (close_f < ema_f).fillna(False).astype('bool')
    if side == 'short':
        return (close_f > ema_f).fillna(False).astype('bool')
    msg = f'Unsupported side: {side!r}'
    raise ValueError(msg)
