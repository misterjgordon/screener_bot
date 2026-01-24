.PHONY: help install format format-file lint lint-file type-check type-check-file run check-trade screener clean smb-install smb-reload smb-start smb-stop smb-status smb-logs smb-unload

# Default target
help:
	@echo "Available commands:"
	@echo "  make install              - Install dependencies with uv"
	@echo "  make format               - Format all Python code (ty, ruff, autopep8)"
	@echo "  make format-file FILE=... - Format specific file (e.g., make format-file FILE=check_trade.py)"
	@echo "  make lint                 - Run ruff linter on all files"
	@echo "  make lint-file FILE=...   - Run ruff linter on specific file"
	@echo "  make type-check           - Run ty type checker on all files"
	@echo "  make type-check-file FILE=... - Run ty type checker on specific file"
	@echo "  make run                  - Run main.py"
	@echo "  make check-trade          - Run check_trade.py"
	@echo "  make smb                  - Run smb_screener.py"
	@echo "  make jobot                - Run jobot.py"
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
format:
	@echo "Formatting with ty..."
	uv run --frozen ty format .
	@echo "Formatting with ruff..."
	uv run --frozen ruff format .
	@echo "Formatting with autopep8..."
	uv run --frozen autopep8 --in-place --recursive --aggressive --aggressive .

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

# Run main script
run:
	python main.py

# Run check_trade script
check-trade:
	python check_trade.py

# Run screener script
smb:
	python smb_screener.py

# Run jobot script
jobot:
	python jobot.py


# Clean Python cache files
clean:
	find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	@echo "Cleaned Python cache files"

# SMB Screener LaunchAgent management
smb-install:
	@echo "Installing SMB Screener LaunchAgents..."
	@cp com.smb.screener.start.plist ~/Library/LaunchAgents/
	@cp com.smb.screener.stop.plist ~/Library/LaunchAgents/
	@launchctl load ~/Library/LaunchAgents/com.smb.screener.start.plist 2>/dev/null || true
	@launchctl load ~/Library/LaunchAgents/com.smb.screener.stop.plist 2>/dev/null || true
	@echo "✓ LaunchAgents installed and loaded"
	@echo "  - Start agent: starts at 6:30 AM on weekdays (or use 'make smb-start' to start now)"
	@echo "  - Stop agent: stops at 1:00 PM on weekdays"
	@echo "  - Prevents Mac from sleeping while bot runs"
	@echo "  - Make sure RUN_MODE is set to 'poll' in smb_screener.py"

smb-reload:
	@echo "Reloading SMB Screener LaunchAgents..."
	@launchctl unload ~/Library/LaunchAgents/com.smb.screener.start.plist 2>/dev/null || true
	@launchctl unload ~/Library/LaunchAgents/com.smb.screener.stop.plist 2>/dev/null || true
	@cp com.smb.screener.start.plist ~/Library/LaunchAgents/
	@cp com.smb.screener.stop.plist ~/Library/LaunchAgents/
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
	@printf "%-8s %-12s %s\n" "PID" "EXIT STATUS" "LABEL"
	@launchctl list | grep smb | awk '{printf "%-8s %-12s %s\n", $$1, $$2, $$3}' || echo "  Not loaded"

smb-logs:
	@echo "Following SMB Screener logs (Ctrl+C to exit)..."
	@tail -f logs/smb_screener.log

smb-unload:
	@echo "Unloading SMB Screener LaunchAgents..."
	@launchctl unload ~/Library/LaunchAgents/com.smb.screener.start.plist 2>/dev/null || true
	@launchctl unload ~/Library/LaunchAgents/com.smb.screener.stop.plist 2>/dev/null || true
	@rm -f ~/Library/LaunchAgents/com.smb.screener.start.plist
	@rm -f ~/Library/LaunchAgents/com.smb.screener.stop.plist
	@echo "✓ LaunchAgents unloaded and removed"
