"""Launcher for ``trading.ib_us_stock_universe_cli`` (``sys.path`` for repo root)."""

import runpy
import sys
from pathlib import Path


def _run() -> None:
    p_repo = Path(__file__).resolve().parents[1]
    if str(p_repo) not in sys.path:
        sys.path.insert(0, str(p_repo))
    runpy.run_module('trading.ib_us_stock_universe_cli', run_name='__main__')


if __name__ == '__main__':
    _run()
