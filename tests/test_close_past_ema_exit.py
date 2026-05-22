"""``close_past_ema`` other-exit series (long: close below EMA)."""

import pandas as pd

from strategies.exit.other.close_past_ema import close_past_ema_exit_series


def test_close_past_ema_long_exit_when_close_below_ema() -> None:
    close = pd.Series([101.0, 99.0, 100.0])
    ema = pd.Series([100.0, 100.0, 100.0])
    out = close_past_ema_exit_series(close, ema, side='long')

    flags = [bool(out.iloc[i]) for i in range(3)]

    print(
        '**summary for close_past_ema:**\n'
        f'flags = {flags}'
    )

    assert flags == [False, True, False]


def test_close_past_ema_short_exit_when_close_above_ema() -> None:
    close = pd.Series([99.0, 101.0, 100.0])
    ema = pd.Series([100.0, 100.0, 100.0])
    out = close_past_ema_exit_series(close, ema, side='short')

    flags = [bool(out.iloc[i]) for i in range(3)]

    print(
        '**summary for close_past_ema short:**\n'
        f'flags = {flags}'
    )

    assert flags == [False, True, False]
