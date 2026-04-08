#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT='/Users/joel/Github/trading'
UV_BIN='/Users/joel/.local/bin/uv'

# Use local machine date (PST/PDT) for the scheduled morning run.
trade_date="$(date +%F)"

cd "$REPO_ROOT"
"$UV_BIN" run --frozen python -m watchlist.run_sources --date "$trade_date"

if ! "$UV_BIN" run --frozen python -m watchlist.run_watchlist_report --date "$trade_date"; then
    echo 'watchlist report (Claude API) failed; ingest above completed.' >&2
fi
