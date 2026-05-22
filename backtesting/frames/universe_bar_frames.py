"""Collection of per-symbol bar frames for a backtest universe."""

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from backtesting.frames.symbol_bar_frame import SymbolBarFrame


class UniverseBarFrames:
    """Holds one ``SymbolBarFrame`` per symbol after the vector prep phase.

    Internally backed by a ``dict[str, SymbolBarFrame]``; raw dict access is not
    exposed so callers cannot bypass the typed API.
    """

    def __init__(self, frames: dict[str, 'SymbolBarFrame']) -> None:
        self._frames = {sym.upper(): frame for sym, frame in frames.items()}

    @classmethod
    def from_list(cls, frames: list['SymbolBarFrame']) -> 'UniverseBarFrames':
        """Build from a list of frames (symbols are taken from each frame's ``symbol``)."""
        return cls({f.symbol: f for f in frames})

    @property
    def symbols(self) -> list[str]:
        """Sorted symbol list."""
        return sorted(self._frames)

    def get(self, symbol: str) -> 'SymbolBarFrame':
        """Return the frame for ``symbol`` (uppercase-normalised).

        Raises ``KeyError`` if the symbol is not in the universe.
        """
        key = symbol.strip().upper()
        if key not in self._frames:
            msg = f"Symbol '{key}' not found in universe ({self.symbols})"
            raise KeyError(msg)
        return self._frames[key]

    def __len__(self) -> int:
        return len(self._frames)

    def iter_frames(self) -> tuple['SymbolBarFrame', ...]:
        """Frames in sorted symbol order (each frame's ``bars`` hold the canonical data)."""
        return tuple(self._frames[sym] for sym in self.symbols)

    def map(self, fn: Callable[['SymbolBarFrame'], 'SymbolBarFrame']) -> 'UniverseBarFrames':
        """Apply ``fn`` to each frame and return a new ``UniverseBarFrames``."""
        return UniverseBarFrames({sym: fn(frame) for sym, frame in self._frames.items()})

    def to_dataframe_multiindex(self) -> pd.DataFrame:
        """Concatenate all frames into a single DataFrame with ``(symbol, timestamp)`` index.

        Useful for notebook exploration.  For large universes prefer ``export_dir``.
        """
        if not self._frames:
            return pd.DataFrame()
        parts = []
        for sym in self.symbols:
            df = self._frames[sym].bars.copy()
            df.insert(0, '_symbol', sym)
            parts.append(df)
        df_all = pd.concat(parts, ignore_index=True)
        return df_all.set_index(['_symbol', 'timestamp']).rename_axis(
            index=['symbol', 'timestamp']
        )

    def export_dir(self, p_dir: Path) -> list[Path]:
        """Write each symbol's bars to ``{p_dir}/{symbol}.parquet``.

        Creates ``p_dir`` if it does not exist.  Returns the list of paths written.
        """
        p_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for sym in self.symbols:
            p_out = p_dir / f'{sym}.parquet'
            self._frames[sym].to_parquet(p_out)
            written.append(p_out)
        return written
