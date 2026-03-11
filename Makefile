.PHONY: help install format format-file lint lint-file type-check type-check-file run screener clean smb-install smb-reload smb-start smb-stop smb-status smb-logs smb-unload

# Default target
help:
	@echo "Available commands:"
	@echo "  make install              - Install dependencies with uv"
	@echo "  make format               - Format all Python code (ty, ruff, autopep8)"
	@echo "  make format-file FILE=... - Format specific file (e.g., make format-file FILE=trading/smb_screener.py)"
	@echo "  make lint                 - Run ruff linter on all files"
	@echo "  make lint-file FILE=...   - Run ruff linter on specific file"
	@echo "  make type-check           - Run ty type checker on all files"
	@echo "  make type-check-file FILE=... - Run ty type checker on specific file"
	@echo "  make run                  - Run trading/smb_screener.py"
	@echo "  make smb                  - Run trading/smb_screener.py"

	@echo "  make clean                - Clean Python cache files"
	@echo ""
	@echo "SMB Screener LaunchAgent commands:"
	@echo "  make smb-install          - Install and load both LaunchAgents (start at 6:30 AM, stop at 1:00 PM)"
	@echo "  make smb-reload           - Reload LaunchAgents (unload then load, for after editing plists)"
	@echo "  make smb-start            - Start the bot now"
	@echo "  make smb-stop             - Stop the bot"
	@echo "  make smb-status           - Check if LaunchAgents are running"
	@echo "  make smb-logs             - View LaunchAgent logs (follow mode)"
	@echo "  make smb-unload           - Unload and remove all LaunchAgents"

# Install dependencies
install:
	uv sync

# Format code (following spec.md pattern)
format:  ## run autopep, isort, ruff on $(code); ty on changed files vs webdev
	@./scripts/format_python_code.sh $(code)
# Format specific file
format-file:
	@if [ -z "$(FILE)" ]; then \
		echo "Error: FILE parameter required. Usage: make format-file FILE=path/to/file.py"; \
		exit 1; \
	fi
	@echo "Formatting $(FILE) with ty..."
	uv run --frozen ty format $(FILE)
	@echo "Formatting $(FILE) with ruff..."
	uv run --frozen ruff format $(FILE)
	@echo "Formatting $(FILE) with autopep8..."
	uv run --frozen autopep8 --in-place --aggressive --aggressive $(FILE)

# Lint code
lint:
	uv run --frozen ruff check .

# Lint specific file
lint-file:
	@if [ -z "$(FILE)" ]; then \
		echo "Error: FILE parameter required. Usage: make lint-file FILE=path/to/file.py"; \
		exit 1; \
	fi
	uv run --frozen ruff check $(FILE)

# Type check
type-check:
	uv run --frozen ty check .

# Type check specific file
type-check-file:
	@if [ -z "$(FILE)" ]; then \
		echo "Error: FILE parameter required. Usage: make type-check-file FILE=path/to/file.py"; \
		exit 1; \
	fi
	uv run --frozen ty check $(FILE)

# Run main script (uv run + -m so trading package is found from repo root)
run:
	uv run python -m trading.smb_screener

# Run screener script
smb:
	uv run python -m trading.smb_screener

# Clean Python cache files
clean:
	find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	@echo "Cleaned Python cache files"

# SMB Screener LaunchAgent management
# Plists use __REPO_ROOT__; we substitute with $(CURDIR) so they work after moving the repo.
REPO_ROOT := $(CURDIR)
smb-install:
	@echo "Installing SMB Screener LaunchAgents (repo root: $(REPO_ROOT))..."
	@mkdir -p logs
	@sed 's|__REPO_ROOT__|$(REPO_ROOT)|g' com.smb.screener.start.plist > ~/Library/LaunchAgents/com.smb.screener.start.plist
	@sed 's|__REPO_ROOT__|$(REPO_ROOT)|g' com.smb.screener.stop.plist > ~/Library/LaunchAgents/com.smb.screener.stop.plist
	@launchctl load ~/Library/LaunchAgents/com.smb.screener.start.plist 2>/dev/null || true
	@launchctl load ~/Library/LaunchAgents/com.smb.screener.stop.plist 2>/dev/null || true
	@echo "✓ LaunchAgents installed and loaded"
	@echo "  - Start agent: starts at 6:30 AM on weekdays (or use 'make smb-start' to start now)"
	@echo "  - Stop agent: stops at 1:00 PM on weekdays"
	@echo "  - Prevents Mac from sleeping while bot runs"
	@echo "  - Make sure RUN_MODE is set to 'poll' in trading/smb_screener.py"

smb-reload:
	@echo "Reloading SMB Screener LaunchAgents (repo root: $(REPO_ROOT))..."
	@launchctl unload ~/Library/LaunchAgents/com.smb.screener.start.plist 2>/dev/null || true
	@launchctl unload ~/Library/LaunchAgents/com.smb.screener.stop.plist 2>/dev/null || true
	@mkdir -p logs
	@sed 's|__REPO_ROOT__|$(REPO_ROOT)|g' com.smb.screener.start.plist > ~/Library/LaunchAgents/com.smb.screener.start.plist
	@sed 's|__REPO_ROOT__|$(REPO_ROOT)|g' com.smb.screener.stop.plist > ~/Library/LaunchAgents/com.smb.screener.stop.plist
	@launchctl load ~/Library/LaunchAgents/com.smb.screener.start.plist 2>/dev/null || true
	@launchctl load ~/Library/LaunchAgents/com.smb.screener.stop.plist 2>/dev/null || true
	@echo "✓ LaunchAgents reloaded"

smb-start:
	@echo "Starting SMB Screener bot..."
	@launchctl start com.smb.screener.start
	@echo "✓ Bot started"
	@echo "Watch logs with: make smb-logs"

smb-stop:
	@echo "Stopping SMB Screener bot..."
	@launchctl stop com.smb.screener.start
	@echo "✓ Bot stopped"

smb-status:
	@echo "SMB Screener LaunchAgent status:"
	@printf "%-15s %-12s %s\n" "STATUS" "EXIT CODE" "LABEL"
	@launchctl list | grep smb | awk '{ \
		if ($$1 == "-") { \
			status = "Loaded (idle)"; \
		} else if ($$1 > 0) { \
			status = "Running (PID " $$1 ")"; \
		} else { \
			status = "Unknown"; \
		} \
		printf "%-15s %-12s %s\n", status, $$2, $$3 \
	}' || echo "  Not loaded"
	@echo ""
	@echo "Last screener activity:"
	@LATEST_LOG=$$(find logs/screener_log -name "*.log" ! -name "*.error.log" -type f 2>/dev/null | xargs ls -t 2>/dev/null | head -1); \
	if [ -n "$$LATEST_LOG" ] && [ -f "$$LATEST_LOG" ]; then \
		echo "  Log: $$LATEST_LOG"; \
		echo "  Last modified: $$(stat -f '%Sm' -t '%Y-%m-%d %H:%M:%S' "$$LATEST_LOG" 2>/dev/null || stat -c '%y' "$$LATEST_LOG" 2>/dev/null | cut -d. -f1)"; \
		last_line=$$(grep -E "Running in (polling|once)|Attempting IB|✓ IB connected|Execution: processing" "$$LATEST_LOG" | tail -1); \
		if [ -n "$$last_line" ]; then echo "  Last startup/activity: $$last_line"; fi; \
	else \
		echo "  No screener logs found under logs/screener_log/ (screener has not run or log path wrong)"; \
	fi
	@echo ""
	@echo "TWS/IB Gateway Connection Status:"
	@if lsof -i :7497 >/dev/null 2>&1; then \
		echo "  ✓ Port 7497 (TWS Paper): LISTENING"; \
		if lsof -i :7497 | grep -q ESTABLISHED; then \
			echo "  ✓ Active connection detected on port 7497"; \
		else \
			echo "  ⚠  Port 7497 listening but no active connections"; \
		fi; \
	else \
		echo "  ✗ Port 7497 (TWS Paper): NOT LISTENING"; \
	fi
	@if lsof -i :4001 >/dev/null 2>&1; then \
		echo "  ✓ Port 4001 (IB Gateway Paper): LISTENING"; \
		if lsof -i :4001 | grep -q ESTABLISHED; then \
			echo "  ✓ Active connection detected on port 4001"; \
		else \
			echo "  ⚠  Port 4001 listening but no active connections"; \
		fi; \
	else \
		echo "  ✗ Port 4001 (IB Gateway Paper): NOT LISTENING"; \
	fi
	@if lsof -i :7497 >/dev/null 2>&1 || lsof -i :4001 >/dev/null 2>&1 || lsof -i :7496 >/dev/null 2>&1; then \
		tws_pid=$$(lsof -ti :7497 2>/dev/null | head -1); \
		gateway_pid=$$(lsof -ti :4001 2>/dev/null | head -1); \
		if [ -n "$$tws_pid" ]; then \
			echo "  ✓ TWS/Gateway process: RUNNING (PID $$tws_pid on port 7497)"; \
		elif [ -n "$$gateway_pid" ]; then \
			echo "  ✓ TWS/Gateway process: RUNNING (PID $$gateway_pid on port 4001)"; \
		else \
			echo "  ✓ TWS/Gateway process: RUNNING (port detected)"; \
		fi; \
	else \
		echo "  ✗ TWS/Gateway process: NOT RUNNING"; \
	fi
	@echo ""
	@echo "Recent IB Connection Status (from logs):"
	@LATEST_LOG=$$(find logs/screener_log -name "*.log" ! -name "*.error.log" -type f 2>/dev/null | xargs ls -t 2>/dev/null | head -1); \
	if [ -n "$$LATEST_LOG" ] && [ -f "$$LATEST_LOG" ]; then \
		last_conn=$$(grep -E "(Attempting IB|IB connected|✓ IB)" "$$LATEST_LOG" | tail -1); \
		if [ -n "$$last_conn" ]; then echo "  $$last_conn"; else echo "  No connection attempts found in logs"; fi; \
	else \
		echo "  No screener logs found"; \
	fi

smb-logs:
	@echo "Following SMB Screener logs (Ctrl+C to exit)..."
	@TODAY_LOG="logs/screener_log/$$(date +%Y/%m)/$$(date +%Y-%m-%d).log"; \
	if [ ! -f "$$TODAY_LOG" ]; then \
		echo "Today's log not yet created: $$TODAY_LOG"; \
		echo "Creating directory; run the screener or wait for schedule. Tailing most recent log if any."; \
		RECENT=$$(find logs/screener_log -name "*.log" ! -name "*.error.log" -type f 2>/dev/null | xargs ls -t 2>/dev/null | head -1); \
		if [ -n "$$RECENT" ]; then tail -f "$$RECENT"; else exit 1; fi; \
	else \
		tail -f "$$TODAY_LOG"; \
	fi

smb-unload:
	@echo "Unloading SMB Screener LaunchAgents..."
	@launchctl unload ~/Library/LaunchAgents/com.smb.screener.start.plist 2>/dev/null || true
	@launchctl unload ~/Library/LaunchAgents/com.smb.screener.stop.plist 2>/dev/null || true
	@rm -f ~/Library/LaunchAgents/com.smb.screener.start.plist
	@rm -f ~/Library/LaunchAgents/com.smb.screener.stop.plist
	@echo "✓ LaunchAgents unloaded and removed"
