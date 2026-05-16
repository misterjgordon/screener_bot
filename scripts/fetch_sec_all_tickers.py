"""Launcher for ``trading.fetch_sec_all_tickers_cli`` (repo root on ``sys.path``)."""

import runpy
import sys
from pathlib import Path


def _run() -> None:
    p_repo = Path(__file__).resolve().parents[1]
    if str(p_repo) not in sys.path:
        sys.path.insert(0, str(p_repo))
    runpy.run_module('trading.fetch_sec_all_tickers_cli', run_name='__main__')


if __name__ == '__main__':
    _run()
