"""Create executions directory and append execution rows to daily CSV files."""

import csv
from datetime import date
from datetime import datetime
from pathlib import Path

from trading.market_timezones import local_zone
from trading.models import Execution

_REPO_ROOT = Path(__file__).resolve().parent.parent
EXECUTIONS_DIR = str(_REPO_ROOT / 'smb_trader_executions')


def format_timestamp(dt: datetime | None = None) -> str:
    """
    Format datetime as a database and Excel-friendly timestamp string.

    Format: YYYY-MM-DD HH:MM:SS (space-separated, seconds precision) in
    America/Vancouver wall time. Matches ``execution_db._parse_timestamp`` and
    ``import_executions``: naive strings round-trip as Pacific, not system local.

    When ``dt`` is None, uses current time in Vancouver (not ``datetime.now()`` alone,
    which follows host TZ and mislabels UTC machines as Pacific when parsed).

    Args:
        dt: Optional datetime; naive values are treated as Vancouver local; aware
            values are converted to Vancouver before formatting.

    Returns:
        str: Formatted timestamp string (24h Pacific wall clock)
    """
    if dt is None:
        dt = datetime.now(local_zone())
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_zone())
    else:
        dt = dt.astimezone(local_zone())
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def vancouver_today() -> date:
    """Calendar date in the configured local zone (for daily CSV filename)."""
    return datetime.now(local_zone()).date()


def ensure_executions_dir() -> None:
    """Ensure the executions directory exists."""
    Path(EXECUTIONS_DIR).mkdir(parents=True, exist_ok=True)


def get_executions_filename() -> str:
    """Daily CSV path using America/Vancouver calendar date (not host ``date.today()``)."""
    today = vancouver_today()
    filename = f"executions_{today.strftime('%Y-%m-%d')}.csv"
    return str(Path(EXECUTIONS_DIR) / filename)


def save_execution_to_csv(
    trader: str,
    symbol: str,
    change_type: str,
    net_side: str,
    delta_magnitude: float,
    entry_price: float | None = None,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    order_id: str | None = None,
    filled_price: float | None = None,
    timestamp: str | None = None,
    shares: int | None = None,
    total_risk: float | None = None,
    risk_per_share: float | None = None,
    risk_percent: float | None = None,
) -> None:
    """Append one execution row; timestamp column is Pacific wall time (see ``format_timestamp``)."""
    ensure_executions_dir()
    filename = get_executions_filename()
    if timestamp is None:
        timestamp = format_timestamp()
    file_exists = Path(filename).exists()
    execution = Execution(
        timestamp=timestamp,
        trader=trader,
        symbol=symbol,
        change_type=change_type,
        net_side=net_side,
        delta_magnitude=delta_magnitude,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        order_id=order_id,
        filled_price=filled_price,
        shares=shares,
        total_risk=total_risk,
        risk_per_share=risk_per_share,
        risk_percent=risk_percent,
    )
    with Path(filename).open('a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=Execution.csv_fieldnames())
        if not file_exists:
            writer.writeheader()
        writer.writerow(execution.to_csv_row())
