"""Print position snapshot table for SMB screener."""

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.models import PositionSummary


def _get_field(r: 'PositionSummary', field: str) -> str | int | float | bool | None:
    """Get field value from PositionSummary by name."""
    if field == 'trader':
        return r.trader
    if field == 'is_long_term':
        return r.is_long_term
    if field == 'symbol':
        return r.symbol
    if field == 'instrument_type':
        return r.instrument_type
    if field == 'net_side':
        return r.net_side
    if field == 'total_magnitude':
        return r.total_magnitude
    if field == 'delta_magnitude':
        return r.delta_magnitude
    if field == 'change_type':
        return r.change_type
    raise KeyError(f'Unknown PositionSummary field: {field}')


def print_position_table(summary_rows: list['PositionSummary'], hide_flat: bool = True) -> None:
    """Print a table of positions: Trader | LT | Symbol | Type | Side | Mag | MagChg | Change."""
    rows_to_show: list[PositionSummary] = []
    for r in summary_rows:
        if hide_flat and r.net_side == 'flat' and r.total_magnitude == 0:
            if r.change_type and r.change_type in ['CLOSE', 'NEW', 'ADD', 'TRIM', 'FLIP']:
                rows_to_show.append(r)
        else:
            rows_to_show.append(r)
    column_specs = [
        {'header': 'Trader', 'field': 'trader', 'width': 25, 'align': '<'},
        {'header': 'LT', 'field': 'is_long_term', 'width': 3, 'align': '<'},
        {'header': 'Symbol', 'field': 'symbol', 'width': 30, 'align': '<'},
        {'header': 'Type', 'field': 'instrument_type', 'width': 8, 'align': '<'},
        {'header': 'Side', 'field': 'net_side', 'width': 6, 'align': '<'},
        {'header': 'Mag', 'field': 'total_magnitude', 'width': 6, 'align': '>'},
        {'header': 'MagChg', 'field': 'delta_magnitude', 'width': 6, 'align': '>'},
        {'header': 'Change', 'field': 'change_type', 'width': 8, 'align': '<'},
    ]

    def format_cell(value: str | int | float | bool | None, spec: dict) -> str:
        if spec['field'] == 'is_long_term':
            return 'LT' if value else '  '
        if spec['field'] == 'change_type' and value is None:
            return ' ' * spec['width']
        if value is None:
            return 'NA'
        return f'{str(value):{spec["align"]}{spec["width"]}}'

    def build_header_line() -> str:
        return ' '.join(f'{col["header"]:{col["align"]}{col["width"]}}' for col in column_specs)

    def build_divider() -> str:
        return '-' * (sum(int(col['width']) + 1 for col in column_specs) - 1)

    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print(build_header_line())
    print(build_divider())
    for r in rows_to_show:
        cells = [format_cell(_get_field(r, str(spec['field'])), spec) for spec in column_specs]
        print(' '.join(cells))
