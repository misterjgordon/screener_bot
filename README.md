# Automated Trading Bot

A Python-based automated reasearch and investment system that monitors external position data and executes trades through a broker. Built for educational and research purposes using paper trading accounts.

## Important Notes

- **Educational purposes only** - This bot is designed for learning and testing trading automation
- **Paper trading account** - Configured to use Interactive Brokers paper trading account
- **Proprietary logic** - Some trading logic and risk management parameters are not fully documented

## Overview

This project demonstrates a complete trading automation workflow, from position monitoring to order execution. The system continuously tracks position changes from an external API and automatically executes corresponding trades in Interactive Brokers, with built-in risk management and execution tracking.

## What Was Built

### Core Components

**Position Monitoring Bot** (`trading/smb_screener.py`)
- Real-time position monitoring from external trading API
- Session management with cookie persistence
- Change detection system (NEW, ADD, TRIM, CLOSE, FLIP)
- Automated order execution via Interactive Brokers API
- Risk management and position sizing calculations

**Execution Tracking System** (`smbweb/`)
- Django REST API for execution data management
- CSV import functionality for historical analysis
- Database-backed execution logging
- Web interface for tracking and analyzing trades

### Key Features

- **Automated Position Mirroring**: Monitors external positions and mirrors changes in IB account
- **Change Detection**: Compares current positions with previous snapshots to identify changes
- **Order Management**: Places bracket orders (entry, stop loss, take profit) automatically
- **Risk Management**: Position sizing based on risk parameters and account constraints
- **Trailing Stops**: Calculates trailing stops from historical price data
- **ADR-Based Targets**: Uses Average Daily Range for stop loss and take profit calculations
- **Execution Logging**: Comprehensive CSV logging of all execution events
- **Session Persistence**: Maintains authenticated sessions across restarts

### Technical Implementation

**Architecture**
- Polling-based monitoring system (configurable interval)
- State management via JSON snapshots
- Modular design with separation of concerns
- Error handling and connection recovery

**Integration Points**
- External trading API (authentication via credentials)
- Interactive Brokers API (ib_async library)
- Django web framework for data management
- PostgreSQL database for execution storage

**Risk Management**
- Daily loss limits
- Position sizing based on risk per trade
- Automatic stop loss placement
- Take profit targets based on volatility metrics
- Gap detection and position size adjustment

## Project Structure

```
├── alerts/                  # Automation triggers (tracked in git)
│   └── new_positions/       # One JSON per SMB NEW position
├── docs/                    # Guides (PostgreSQL, GitHub, snapshot DB, etc.)
├── resources/               # Gitignored: cookies, position snapshots
├── scripts/                 # format_python_code.sh, backfills, etc.
├── smbweb/                  # Django web application
│   ├── apps/executions/     # Execution tracking app
│   ├── apps/market/         # Symbols, OHLCV bars, Alpaca bar import
│   ├── views/               # API endpoints
│   └── manage.py            # Django management script
├── strategies/              # Indicators, bar patterns, fundamentals
├── trading/                 # Main bot and broker integration
│   ├── smb_screener.py      # Main monitoring and execution bot
│   ├── run_screener.py      # Polling/once entrypoint
│   └── integrations/      # External data (e.g. Alpaca bars)
├── watchlist/               # Morning sources, desk-day tickers, AI report flow
│   ├── sources/             # Per-source fetchers (gameplan, briefing, etc.)
│   ├── prompts/             # LLM prompt templates
│   └── repository/          # Gitignored: daily JSON/Markdown outputs
├── tests/                   # Unit tests for trading and strategies
├── logs/                    # Gitignored
├── smb_trader_executions/   # Gitignored execution CSVs
├── pyproject.toml           # Project dependencies
└── uv.lock                  # Locked dependency versions (uv)
```

## Technology Stack

- **Python 3.13+**
- **ib_async** - Interactive Brokers API wrapper
- **Django** - Web framework for execution tracking
- **PostgreSQL** - Database for execution data
- **requests** - HTTP client for API interactions
- **pandas** - Data analysis (for execution imports)

## Development Highlights

### Session Management
Implemented cookie-based session persistence to minimize authentication overhead. Sessions are validated before use and automatically recreated when expired.

### Change Detection Algorithm
Developed a snapshot comparison system that tracks position changes across multiple dimensions:
- Position magnitude changes
- Side changes (long/short/flat)
- New positions vs. additions vs. trims
- Position closures and flips

### Order Execution Logic
Built comprehensive order execution system supporting:
- Bracket orders with entry, stop loss, and take profit
- Scaling orders for position additions
- Market orders for position exits
- Child order updates for position modifications

### Risk Calculations
Implemented multiple risk management approaches:
- Trailing stop calculation from historical bars
- ADR-based stop loss and take profit
- Position sizing from risk parameters
- Gap detection and position adjustment
- Breaout bar detection to avoid entering positions at the end of the candle

## Execution Tracking

All execution events are logged to CSV files with timestamps, trader information, symbols, change types, prices, and order IDs. This data can be imported into the Django application for analysis and reporting.

## Configuration

The bot is highly configurable via script constants:
- Run modes: single execution, continuous polling, or disabled
- Trading activation toggle
- Per-trader enable/disable flags
- Risk management parameters
- Connection settings

## Notes

- Designed for paper trading accounts only
- Requires Interactive Brokers TWS or Gateway
- Uses environment variables for credentials
- Some trading logic parameters are proprietary

## License

Proprietary - Internal use only.
