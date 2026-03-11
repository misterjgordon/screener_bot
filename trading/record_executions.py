"""Create executions directory and append execution rows to daily CSV files."""

import csv
from datetime import date
from datetime import datetime
from pathlib import Path

from trading.models import Execution

_REPO_ROOT = Path(__file__).resolve().parent.parent
EXECUTIONS_DIR = str(_REPO_ROOT / 'smb_trader_executions')


def format_timestamp(dt: datetime | None = None) -> str:
    """
    Format datetime as a database and Excel-friendly timestamp string.

    Format: YYYY-MM-DD HH:MM:SS (space-separated, seconds precision)
    This format is:
    - Recognized by Excel when importing CSV
    - Compatible with most databases (PostgreSQL, MySQL, SQLite, etc.)
    - Sortable and filterable in both Excel and databases

    Args:
        dt: datetime object (defaults to current time if None)

    Returns:
        str: Formatted timestamp string
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def ensure_executions_dir() -> None:
    """Ensure the executions directory exists."""
    Path(EXECUTIONS_DIR).mkdir(parents=True, exist_ok=True)


def get_executions_filename() -> str:
    """Get the executions CSV filename for today."""
    today = date.today()
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
    timestamp: str | None = None,
    shares: int | None = None,
    total_risk: float | None = None,
    risk_per_share: float | None = None,
) -> None:
    """Save execution data to CSV file using Execution schema."""
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
        shares=shares,
        total_risk=total_risk,
        risk_per_share=risk_per_share,
    )
    with Path(filename).open('a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=Execution.csv_fieldnames())
        if not file_exists:
            writer.writeheader()
        writer.writerow(execution.to_csv_row())
