#!/usr/bin/env python3
"""Report drift between ``*_series`` functions and ``indicator_catalog.yaml``.

Scans ``strategies/indicators/*.py`` for ``def <name>_series`` and compares to the
catalog. Does not modify the catalog automatically.

Example::

    uv run --frozen python scripts/sync_indicator_catalog.py
"""

import ast
import sys
from pathlib import Path

_p_repo = Path(__file__).resolve().parent.parent
if str(_p_repo) not in sys.path:
    sys.path.insert(0, str(_p_repo))

from backtesting.indicators.indicator_catalog_load import P_INDICATOR_CATALOG  # noqa: E402
from backtesting.indicators.indicator_catalog_load import catalog_entry_by_id  # noqa: E402


def _discover_series_functions() -> dict[str, str]:
    """Map ``module:callable`` for each ``*_series`` function under strategies/indicators."""
    p_indicators = _p_repo / 'strategies' / 'indicators'
    found: dict[str, str] = {}
    for p_py in sorted(p_indicators.glob('*.py')):
        if p_py.name.startswith('_'):
            continue
        tree = ast.parse(p_py.read_text(encoding='utf-8'))
        module_name = f'strategies.indicators.{p_py.stem}'
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.endswith('_series'):
                continue
            key = f'{module_name}:{node.name}'
            found[node.name] = key
    return found


def main() -> None:
    series_by_name = _discover_series_functions()
    catalog = catalog_entry_by_id()
    catalog_fns = {entry.series_fn for entry in catalog.values()}

    missing_from_catalog = [
        fn for fn in sorted(series_by_name.values()) if fn not in catalog_fns
    ]
    stale_in_catalog = sorted(catalog_fns - set(series_by_name.values()))

    print(f'catalog_path = {P_INDICATOR_CATALOG}')
    print(f'series_functions_found = {len(series_by_name)}')
    print(f'catalog_entries = {len(catalog)}')
    print()
    print('missing_from_catalog (implement in YAML):')
    for fn in missing_from_catalog:
        print(f'  - {fn}')
    if not missing_from_catalog:
        print('  (none)')
    print()
    print('stale_in_catalog (no matching *_series in repo):')
    for fn in stale_in_catalog:
        print(f'  - {fn}')
    if not stale_in_catalog:
        print('  (none)')


if __name__ == '__main__':
    main()
