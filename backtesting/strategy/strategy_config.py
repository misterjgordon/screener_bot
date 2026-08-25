"""Pydantic models for strategy YAML (``strategies/configs/*.yaml``)."""

import re
from typing import Annotated
from typing import Literal

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from backtesting.indicators.indicator_catalog_load import topological_indicator_order

SessionLabel = Literal['PM', 'RTH', 'AH']
TriggerOp = Literal['cross_above', 'cross_below']
FilterOp = Literal['>=', '<=', '>', '<', '==', '!=']
DayBoundary = Literal['session']
EntryRule = Literal['first']
SizingMethod = Literal['fixed_dollars']

_CLOCK_RE = re.compile(r'^\d{2}:\d{2}$')


class SessionConfig(BaseModel):
    """Exchange session gate for ``signal_eligible`` (desk clock in ``timezone``)."""

    allowed_sessions: tuple[SessionLabel, ...]
    intraday_start: str
    intraday_end: str
    timezone: str

    @field_validator('intraday_start', 'intraday_end')
    @classmethod
    def _validate_clock(cls, value: str) -> str:
        if not _CLOCK_RE.match(value):
            msg = f'Expected HH:MM clock string, got {value!r}'
            raise ValueError(msg)
        return value


class TriggerRule(BaseModel):
    """Edge trigger evaluated on consecutive bars (arms entry setup).

    One of ``ref_column`` (another bar column) or ``ref_value`` (constant) must be set
    for ``cross_above``/``cross_below`` ops.
    """

    id: str
    column: str
    op: TriggerOp
    ref_column: str | None = None
    ref_value: float | None = None

    @model_validator(mode='after')
    def _cross_requires_ref(self) -> 'TriggerRule':
        if self.op in ('cross_above', 'cross_below') and self.ref_column is None and self.ref_value is None:
            msg = f'Trigger {self.id!r} with op={self.op!r} requires ref_column or ref_value'
            raise ValueError(msg)
        return self


class FilterRule(BaseModel):
    """Level filter evaluated on the entry bar."""

    id: str
    column: str
    op: FilterOp
    value: float | int | bool


class StopLossPctFromEntry(BaseModel):
    """Stop loss as fraction from entry fill price (long MVP)."""

    type: Literal['pct_from_entry']
    pct: float


class TakeProfitPctFromEntry(BaseModel):
    """Take profit as fraction from entry fill price (long MVP)."""

    type: Literal['pct_from_entry']
    pct: float


StopLossConfig = Annotated[
    StopLossPctFromEntry,
    Field(discriminator='type'),
]

TakeProfitConfig = Annotated[
    TakeProfitPctFromEntry,
    Field(discriminator='type'),
]


class OtherExitEndOfSession(BaseModel):
    """Close at market on last bar of allowed session (sim applies session semantics)."""

    id: str
    type: Literal['end_of_session']


class OtherExitClosePastEma(BaseModel):
    """Exit when close is past EMA (long: below; short: above)."""

    id: str
    type: Literal['close_past_ema']
    side: Literal['long', 'short']
    ema_column: str


OtherExitRule = Annotated[
    OtherExitEndOfSession | OtherExitClosePastEma,
    Field(discriminator='type'),
]


class SizingConfig(BaseModel):
    """Position sizing for entries."""

    method: SizingMethod
    amount: float


class StrategyConfig(BaseModel):
    """Full strategy document loaded from YAML."""

    id: str
    version: str
    signal_timeframe_minutes: int
    session_config: SessionConfig
    triggers: tuple[TriggerRule, ...]
    filters: tuple[FilterRule, ...]
    arming_window: int
    day_boundary: DayBoundary
    entry_rule: EntryRule
    stop_loss: StopLossConfig
    take_profit: TakeProfitConfig
    other_exits: tuple[OtherExitRule, ...] = ()
    sizing: SizingConfig
    conditions: tuple[str, ...] = ()

    @field_validator('arming_window')
    @classmethod
    def _arming_window_positive(cls, value: int) -> int:
        if value < 1:
            msg = f'arming_window must be >= 1, got {value}'
            raise ValueError(msg)
        return value

    def referenced_bar_columns(self) -> tuple[str, ...]:
        """Distinct bar columns referenced by triggers and filters."""
        cols: list[str] = []
        for rule in self.triggers:
            cols.append(rule.column)
            if rule.ref_column is not None:
                cols.append(rule.ref_column)
        for rule in self.filters:
            cols.append(rule.column)
        for rule in self.other_exits:
            if isinstance(rule, OtherExitClosePastEma):
                cols.append(rule.ema_column)
        seen: set[str] = set()
        ordered: list[str] = []
        for col in cols:
            if col in seen:
                continue
            ordered.append(col)
            seen.add(col)
        return tuple(ordered)

    def indicator_ids_for_pipeline(
        self,
        default_indicator_ids: tuple[str, ...],
        registry_ids: frozenset[str],
    ) -> tuple[str, ...]:
        """Registry indicator ids to run: defaults plus referenced columns that are catalog ids."""
        merged: list[str] = list(default_indicator_ids)
        seen = set(merged)
        for col in self.referenced_bar_columns():
            if col not in registry_ids or col in seen:
                continue
            merged.append(col)
            seen.add(col)
        return topological_indicator_order(tuple(merged))
