"""Pydantic models for ``indicator_catalog.yaml``."""

from typing import Literal
from typing import Protocol

import pandas as pd
from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator

IndicatorCatalogParam = int | float | str | bool
IndicatorSeriesKwarg = pd.Series | pd.DataFrame | IndicatorCatalogParam


class IndicatorSeriesFn(Protocol):
    """Catalog ``series_fn`` signature: named inputs in, one output series."""

    def __call__(self, **kwargs: IndicatorSeriesKwarg) -> pd.Series: ...


IndicatorKindLiteral = Literal['indicator', 'session_column']
ParamKindLiteral = Literal['bar_periods', 'calendar_days']


class IndicatorCatalogEntry(BaseModel):
    """One indicator row from ``indicator_catalog.yaml``."""

    id: str
    kind: IndicatorKindLiteral
    description: str
    version: str
    series_fn: str
    inputs: dict[str, str]
    params: dict[str, IndicatorCatalogParam]
    outputs: tuple[str, ...]
    requires: tuple[str, ...] = ()
    requires_daily_bars: bool = False
    requires_history_bars: bool = False
    daily_inputs: dict[str, str] = Field(default_factory=dict)
    param_kind: ParamKindLiteral = 'bar_periods'
    bar_interval_minutes: int = 1


class IndicatorCatalogDocument(BaseModel):
    """Root document: default pipeline ids and ``indicators`` list."""

    default_pipeline_ids: tuple[str, ...]
    indicators: tuple[IndicatorCatalogEntry, ...]

    @model_validator(mode='after')
    def _default_pipeline_ids_exist_in_catalog(self) -> 'IndicatorCatalogDocument':
        known = {entry.id for entry in self.indicators}
        unknown = [iid for iid in self.default_pipeline_ids if iid not in known]
        if unknown:
            msg = f'default_pipeline_ids not in indicators: {unknown}'
            raise ValueError(msg)
        return self
