"""Outcome of loading a symbol universe through the vector prep pipelines."""

from dataclasses import dataclass


@dataclass(frozen=True)
class UniverseLoadReport:
    """Per-symbol load results for one cold window + strategy prep pass."""

    requested_symbols: tuple[str, ...]
    loaded_symbols: tuple[str, ...]
    skipped_no_parquet: tuple[str, ...]
    skipped_empty_window: tuple[str, ...]
    skipped_errors: tuple[str, ...]
    messages: tuple[str, ...]

    @property
    def loaded_count(self) -> int:
        return len(self.loaded_symbols)

    @property
    def skipped_count(self) -> int:
        return (
            len(self.skipped_no_parquet)
            + len(self.skipped_empty_window)
            + len(self.skipped_errors)
        )
