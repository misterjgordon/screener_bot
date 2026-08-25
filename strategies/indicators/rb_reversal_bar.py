"""Rubberband reversal (snap) bar: 2-min signal for the rubber band scalp setup.

Identifies the highest-priority qualifying 2-min bar per RTH session. True for that bar only
(newest-first scan among top-N range bars). Designed to run on the 2-min resampled frame —
set ``bar_interval_minutes: 2`` in the catalog entry.

Qualification gates (in order):
    1. Bar must be in the inner RTH window (exclude first/last ``exclude_edge_bars`` RTH bars).
    2. Bar must be in top-N by range within that inner window.
    3. Bar color (close vs open): green → long candidate, red → short candidate, doji → skip.
    4. Session extension in bar-color direction >= ``atr_extension_min`` ATR (``extension_long``
       for green, ``extension_short`` for red).
    5. Prior bar break: green → ``high > prior_high``; red → ``low < prior_low``.
    6. RVOL >= ``rvol_min``.
    7. Bar range (high − low) > prior bar range.

Scan visits top-N bars newest-first; marks the first match and stops.
"""

import pandas as pd

_PRIOR_BARS_FOR_BREAK = 1


def rb_reversal_bar_series(
    open: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    session: pd.Series,
    trading_date: pd.Series,
    extension_long: pd.Series,
    extension_short: pd.Series,
    rvol: pd.Series,
    *,
    atr_extension_min: float = 2.0,
    rvol_min: float = 2.0,
    top_sized_bar_count: int = 5,
    exclude_edge_bars: int = 2,
) -> pd.Series:
    """True for the 2-min bar that qualifies as a rubberband reversal bar.

    At most one bar per RTH session is marked True — the newest among the top-N
    range-ranked inner-window bars that passes all qualification gates.

    Parameters
    ----------
    open, high, low, close:
        2-min OHLC prices (aggregated from 1-min by the indicator pipeline).
    session:
        Session label (PM/RTH/AH) for each 2-min bar.
    trading_date:
        ET calendar date per bar.
    extension_long:
        (rth_open − lod) / atr through each bar — down-from-open extension in ATR.
    extension_short:
        (hod − rth_open) / atr through each bar — up-from-open extension in ATR.
    rvol:
        Daily relative volume.
    atr_extension_min:
        Minimum session extension in ATR units required in the bar-color direction.
    rvol_min:
        Minimum daily RVOL to qualify.
    top_sized_bar_count:
        Number of widest-range bars to evaluate in the inner window.
    exclude_edge_bars:
        RTH bars to exclude at each session boundary (opening/closing noise).
    """
    result = pd.Series(False, index=open.index, dtype=bool)

    df = pd.DataFrame({
        'open': open,
        'high': high,
        'low': low,
        'close': close,
        'session': session,
        'trading_date': trading_date,
        'ext_long': extension_long,
        'ext_short': extension_short,
        'rvol': rvol,
    })

    for _, day_df in df.groupby('trading_date', sort=False):
        rth_mask = day_df['session'] == 'RTH'
        rth_orig_idx = day_df.index[rth_mask]
        rth = day_df.loc[rth_orig_idx].reset_index(drop=True)
        rth['_orig_idx'] = rth_orig_idx.to_numpy()
        n = len(rth)

        inner_lo = max(_PRIOR_BARS_FOR_BREAK, exclude_edge_bars)
        inner_hi = n - exclude_edge_bars
        if inner_hi <= inner_lo:
            continue

        rth['_range'] = rth['high'] - rth['low']

        top_n = (
            rth.iloc[inner_lo:inner_hi]
            .assign(_pos=range(inner_lo, inner_hi))
            .sort_values(['_range', '_pos'], ascending=[False, False])
            .head(top_sized_bar_count)
            .sort_values('_pos', ascending=False)
        )

        for _, snap in top_n.iterrows():
            pos = int(snap['_pos'])
            prior = rth.iloc[pos - 1]

            is_green = snap['close'] > snap['open']
            is_red = snap['close'] < snap['open']
            if not is_green and not is_red:
                continue

            ext = snap['ext_long'] if is_green else snap['ext_short']
            if pd.isna(ext) or ext < atr_extension_min:
                continue

            if is_green and snap['high'] <= prior['high']:
                continue
            if is_red and snap['low'] >= prior['low']:
                continue

            rv = snap['rvol']
            if pd.isna(rv) or rv < rvol_min:
                continue

            if snap['_range'] <= prior['_range']:
                continue

            result.loc[snap['_orig_idx']] = True
            break

    return result
