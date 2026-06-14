#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT='/Users/joel/Github/trading'
UV_BIN='/Users/joel/.local/bin/uv'

# Use local machine date (PST/PDT) for the scheduled morning run.
trade_date="$(date +%F)"

cd "$REPO_ROOT"

# Poll every 2m during the launchd window; after 6:10 local, run at most once per desk day.
# Set WATCHLIST_MORNING_FORCE=1 to bypass (debug). Manual ingest: make watchlist-run-now.
if [[ "${WATCHLIST_MORNING_FORCE:-}" != "1" ]]; then
    if ! "$UV_BIN" run --frozen python -m watchlist.morning_schedule --date "$trade_date"; then
        exit 0
    fi
fi

"$UV_BIN" run --frozen python -m watchlist.run_sources --date "$trade_date"

if ! "$UV_BIN" run --frozen python -m watchlist.run_ai_watchlist --date "$trade_date"; then
    echo 'watchlist report (Claude API) failed; ingest above completed.' >&2
fi
