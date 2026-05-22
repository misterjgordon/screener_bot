"""Indicator registry loaded from ``indicator_catalog.yaml``."""

import json
from typing import TYPE_CHECKING

from backtesting.indicators.indicator_catalog_load import load_indicator_catalog_document
from backtesting.indicators.indicator_compute import indicator_spec_from_catalog_entry

if TYPE_CHECKING:
    from backtesting.indicators.indicator_spec import IndicatorSpec


class IndicatorRegistry:
    """Registered indicators keyed by id (built from catalog)."""

    def __init__(self) -> None:
        self._entries: dict[str, IndicatorSpec] = {}

    def register(self, spec: 'IndicatorSpec') -> None:
        """Register an indicator spec."""
        if spec.id in self._entries:
            msg = f'Indicator {spec.id!r} already registered'
            raise ValueError(msg)
        self._entries[spec.id] = spec

    def spec(self, indicator_id: str) -> 'IndicatorSpec':
        """Return the spec for ``indicator_id``."""
        try:
            return self._entries[indicator_id]
        except KeyError as exc:
            msg = f'Unknown indicator id: {indicator_id!r}'
            raise KeyError(msg) from exc

    def ids(self) -> tuple[str, ...]:
        """Sorted registered indicator ids."""
        return tuple(sorted(self._entries))

    def validate_ids(self, indicator_ids: tuple[str, ...]) -> None:
        """Raise ``ValueError`` if any id is not registered."""
        unknown = [iid for iid in indicator_ids if iid not in self._entries]
        if unknown:
            msg = f'Unknown indicator ids: {unknown}'
            raise ValueError(msg)

    def output_columns_for(self, indicator_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Output column names for ``indicator_ids`` in order (deduped)."""
        seen: set[str] = set()
        out: list[str] = []
        for indicator_id in indicator_ids:
            for col in self.spec(indicator_id).outputs:
                if col in seen:
                    continue
                out.append(col)
                seen.add(col)
        return tuple(out)

    def catalog_json(self) -> str:
        """Serialize registry metadata for CLI / future UI."""
        rows = [self._entries[iid].to_catalog_dict() for iid in self.ids()]
        return json.dumps(rows, indent=2)


def _register_from_catalog(registry: IndicatorRegistry) -> None:
    doc = load_indicator_catalog_document()
    for entry in doc.indicators:
        registry.register(indicator_spec_from_catalog_entry(entry))


INDICATOR_REGISTRY = IndicatorRegistry()
_register_from_catalog(INDICATOR_REGISTRY)
