# Trading Bot - SMB Screener

A Python-based trading bot that monitors positions from the SMB API and executes trades through Interactive Brokers (paper trading account).

## ⚠️ Important Notes

- **Educational purposes only** - This bot is designed for learning and testing trading automation
- **Paper trading account** - Configured to use Interactive Brokers paper trading account
- **Proprietary logic** - Some trading logic and risk management parameters are not fully documented

## Features

- **Position Monitoring**: Continuously monitors positions from SMB API
- **Automated Execution**: Executes trades based on position changes (NEW, ADD, TRIM, CLOSE)
- **Risk Management**: Built-in position sizing and stop loss management
- **Execution Tracking**: Logs all executions to CSV files for analysis
- **Session Management**: Handles authentication and session persistence

## Prerequisites

- Python 3.13+
- Interactive Brokers TWS or IB Gateway (paper trading account)
- SMB API credentials (username/password)
- Environment variables configured (see Setup)

## Setup

### 1. Install Dependencies

```bash
# Using uv (recommended)
uv sync

# Or using pip
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create a `.env` file in the project root:

```env
SMB_USERNAME=your_username
SMB_PASSWORD=your_password
```

**⚠️ Never commit `.env` files to version control!**

### 3. Configure Interactive Brokers

1. Start TWS or IB Gateway in paper trading mode
2. Enable API connections in TWS settings
3. Default connection settings:
   - Host: `127.0.0.1`
   - Port: `7497` (TWS paper trading) or `4001` (IB Gateway paper)
   - Client ID: `2` (configurable in script)

### 4. Configure Trading Parameters

Edit `smb_screener.py` to adjust:

- `RUN_MODE`: `"once"`, `"poll"`, or `"off"`
- `INTERVAL_SECONDS`: Polling interval (default: 10 seconds)
- `ACTIVE_TRADING`: Enable/disable automatic order execution
- `TRADER_ENABLED`: Enable/disable specific traders
- `DAILY_STOP`: Maximum daily loss limit (USD)

## Usage

### Run Once

```bash
python smb_screener.py
```

Set `RUN_MODE = "once"` in the script to run a single cycle and exit.

### Polling Mode (Continuous)

```bash
python smb_screener.py
```

Set `RUN_MODE = "poll"` in the script to continuously monitor and execute trades.

### Stop the Bot

Press `Ctrl+C` to gracefully stop the bot. The IB connection will be closed automatically.

## How It Works

1. **Authentication**: Logs into SMB API using credentials from `.env`
2. **Position Fetching**: Retrieves current positions from SMB API
3. **Change Detection**: Compares current positions with previous snapshot
4. **Order Execution**: Places orders in IB based on detected changes:
   - **NEW**: New position entry with bracket order (entry, stop loss, take profit)
   - **ADD**: Adds to existing position
   - **TRIM**: Reduces position size
   - **CLOSE**: Exits entire position
5. **Execution Logging**: Records all executions to CSV files in `smb_trader_executions/`

## File Structure

```
trading/
├── smb_screener.py          # Main bot script
├── position_snapshot.json   # Current position snapshot (auto-generated)
├── smb_cookies.pkl         # Session cookies (auto-generated)
├── smb_trader_executions/  # Execution logs (CSV files)
│   └── executions_YYYY-MM-DD.csv
├── smbweb/                 # Django web app for execution tracking
└── .env                    # Environment variables (not in git)
```

## Execution Logs

Execution logs are saved as CSV files in `smb_trader_executions/` with the following columns:

- `timestamp`: Execution timestamp
- `trader`: Trader name
- `symbol`: Stock symbol
- `change_type`: NEW, ADD, TRIM, CLOSE, FLIP
- `net_side`: long, short, flat
- `delta_magnitude`: Change in position magnitude
- `entry_price`: Entry/limit price (if applicable)
- `stop_price`: Stop loss price (if applicable)
- `take_profit_price`: Take profit price (if applicable)
- `order_id`: IB order ID (if order was placed)

## Risk Management

The bot includes several risk management features:

- **Daily Stop Loss**: Maximum daily loss limit (configurable)
- **Position Sizing**: Calculated based on risk per trade
- **Stop Loss Orders**: Automatic stop loss placement for new positions
- **Take Profit Orders**: Automatic take profit targets based on ADR (Average Daily Range)
- **Trailing Stops**: Uses trailing stop calculation when available

## Troubleshooting

### IB Connection Issues

- Ensure TWS/IB Gateway is running and API is enabled
- Check firewall settings
- Verify port number matches configuration
- Check client ID doesn't conflict with other connections

### Session Expiration

- The bot automatically handles session expiration
- Cookies are saved to `smb_cookies.pkl` for reuse
- If authentication fails, delete `smb_cookies.pkl` and restart

### No Orders Placed

- Check `ACTIVE_TRADING` is set to `True`
- Verify trader is enabled in `TRADER_ENABLED`
- Check IB connection status
- Review execution logs for error messages

## Development

### Django Web App

The `smbweb/` directory contains a Django application for tracking and analyzing executions:

```bash
# Run Django migrations
python manage.py migrate

# Import execution data
python manage.py import_executions

# Start development server
python manage.py runserver
```

## License

Proprietary - For internal use only.

## Disclaimer

This software is for educational purposes only. Trading involves substantial risk of loss. Past performance is not indicative of future results. Use at your own risk.
