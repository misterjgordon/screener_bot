"""Column contract checks for SymbolBarFrame DataFrames.

A ``ColumnContractError`` is raised when a required column is missing or has an
unexpected dtype.  Pipelines call ``check_raw_contract`` immediately after loading
from cold Parquet so errors surface at the boundary, not deep in indicator code.
"""

from typing import TYPE_CHECKING

from trading.storage.ohlcv.ohlcv_schema import OHLCV_COLD_PARQUET_COLUMNS

if TYPE_CHECKING:
    import pandas as pd

# Minimum columns required on a freshly loaded (phase='raw') SymbolBarFrame.
RAW_BAR_COLUMNS: tuple[str, ...] = OHLCV_COLD_PARQUET_COLUMNS

# Engine dtypes after :func:`~backtesting.frames.bar_price_round.round_loaded_bar_prices`
# at cold load (cent-aligned ``float64`` prices; Parquet on disk remains ``float32``).
_RAW_DTYPE_CHECKS: dict[str, str] = {
    'timestamp': 'datetime64[us, UTC]',
    'open': 'float64',
    'high': 'float64',
    'low': 'float64',
    'close': 'float64',
    'volume': 'int32',
    'vwap': 'float64',
}


class ColumnContractError(ValueError):
    """Raised when a DataFrame violates the expected column contract."""


def check_columns_present(df: 'pd.DataFrame', required: tuple[str, ...]) -> None:
    """Raise ``ColumnContractError`` if any column in ``required`` is absent from ``df``."""
    missing = [c for c in required if c not in df.columns]
    if missing:
        msg = f'Missing required columns: {missing}'
        raise ColumnContractError(msg)


def check_raw_contract(df: 'pd.DataFrame') -> None:
    """Validate that ``df`` satisfies the raw cold-bar column and dtype contract.

    Checks presence of all ``RAW_BAR_COLUMNS`` then dtype for each column listed
    in ``_RAW_DTYPE_CHECKS``.  Raises ``ColumnContractError`` on the first violation.
    """
    check_columns_present(df, RAW_BAR_COLUMNS)
    for col, expected_dtype in _RAW_DTYPE_CHECKS.items():
        actual = str(df[col].dtype)
        if actual != expected_dtype:
            msg = f"Column '{col}': expected dtype '{expected_dtype}', got '{actual}'"
            raise ColumnContractError(msg)
