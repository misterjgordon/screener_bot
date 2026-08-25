"""Stable run identifier: first 16 hex chars of SHA-256 over canonical inputs."""

import hashlib
import json
from datetime import date


def compute_run_id(
    strategy_id: str,
    start: date,
    end: date,
    symbols: tuple[str, ...],
    initial_capital: float,
) -> str:
    """Deterministic 16-char hex id from key run inputs.

    Same strategy + date range + symbols + capital always yields the same id,
    making duplicate artifact directories detectable without reading files.
    """
    payload = {
        'strategy_id': strategy_id,
        'start': start.isoformat(),
        'end': end.isoformat(),
        'symbols': sorted(s.strip().upper() for s in symbols),
        'initial_capital': initial_capital,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
