# GitHub Usage Guide for Trading Bot

This guide explains how to commit your trading bot code to GitHub while protecting proprietary information.

## Initial Setup

### 1. Create GitHub Repository

1. Go to [GitHub](https://github.com) and create a new repository
2. Name it something generic like `trading-bot`, `trading-automation`, or `position-monitor`
   - **Avoid specific service names** to reduce discoverability
3. Choose **Private** repository (highly recommended for proprietary code)
4. **Important**: When GitHub asks if you want to "Add README", **leave it unchecked (OFF)**
   - You already have a `README.md` file locally that you want to use
   - If GitHub creates its own README, you'll have conflicts when you push your local files
   - Same applies to .gitignore and license options - leave them unchecked since you already have these files

### 2. Connect Local Repository to GitHub

After creating your repository, GitHub will show you instructions. Since you already have code locally, use the "push an existing repository" option. Here's what each command does:

**Step 1: Add GitHub as your remote repository**
```bash
git remote add origin git@github.com:misterjgordon/screener_bot.git
```
- This connects your local folder to your GitHub repository
- `origin` is just a nickname for your GitHub repo
- Replace `misterjgordon/screener_bot` with your actual username/repo name
- Uses SSH (if you have SSH keys set up) - GitHub will also show an HTTPS option if you prefer

**Step 2: Ensure you're on the main branch**
```bash
git branch -M main
```
- This renames your current branch to `main` (if it's not already)
- GitHub uses `main` as the default branch name (older repos used `master`)
- The `-M` flag forces the rename even if `main` already exists
- If you're already on `main`, this command does nothing (safe to run)

**Step 3: Push your code to GitHub**
```bash
git push -u origin main
```
- This sends all your local commits to GitHub
- `-u` sets up tracking so future `git push` commands know where to go
- `origin main` means "push the main branch to origin (GitHub)"
- After this, your code will be visible on GitHub!

**Complete Example:**
```bash
# 1. Navigate to your project directory
cd /Users/joel/Github/trading

# 2. Initialize git (if not already done)
git init

# 3. Add GitHub as remote (use YOUR username/repo from GitHub)
git remote add origin git@github.com:misterjgordon/screener_bot.git

# 4. Make sure you're on main branch
git branch -M main

# 5. Stage all your files
git add .

# 6. Make your first commit
git commit -m "Initial commit: Trading automation bot"

# 7. Push to GitHub
git push -u origin main
```

**Note**: If you haven't set up SSH keys, GitHub will show an HTTPS URL instead:
```bash
git remote add origin https://github.com/misterjgordon/screener_bot.git
```
HTTPS will prompt for your GitHub username and password (or personal access token).

## What to Commit vs. What to Keep Private

### ✅ Safe to Commit (Public Code)

- `smb_screener.py` - Main bot script (code structure is fine, but see notes below)
- `smbweb/` - Django web application code
- `pyproject.toml` - Dependency configuration
- `README.md` - Documentation (portfolio-focused, no service names)
- `Makefile` - Build automation
- `SETUP_POSTGRESQL.md` - Database setup instructions
- `.gitignore` - Git ignore rules
- `.python-version` - Python version specification

**Note**: File names like `smb_screener.py` contain service references but are acceptable since they're internal. The README avoids these terms for discoverability.

### ❌ Never Commit (Sensitive/Proprietary)

- `.env` - Contains API credentials (already in .gitignore)
- `smb_cookies.pkl` - Session cookies (add to .gitignore)
- `position_snapshot.json` - Current positions (add to .gitignore)
- `smb_trader_executions/*.csv` - Execution logs (may contain sensitive trading data)
- Any files with hardcoded credentials or API keys
- Personal trading strategies or proprietary algorithms

### ⚠️ Review Before Committing

Before committing `smb_screener.py`, consider:

1. **Trading Logic**: The general structure is fine, but if you have proprietary algorithms for:
   - Position sizing calculations
   - Stop loss calculations
   - Entry/exit timing
   - Risk management formulas
   
   Consider abstracting these into a separate module or using environment variables for sensitive parameters.

2. **API Endpoints**: External API endpoints are visible in the code, which is generally acceptable for portfolio purposes. The README avoids mentioning specific service names.

3. **Configuration Values**: Consider moving sensitive defaults to environment variables:
   - `DAILY_STOP` (daily loss limit)
   - `TRADER_ENABLED` (which traders to follow)
   - Risk management percentages

## Step-by-Step: Making Your First Commit

### 1. Update .gitignore

Ensure sensitive files are excluded:

```bash
# Check current .gitignore
cat .gitignore
```

Add these lines if not already present:
```
# Trading bot specific
smb_cookies.pkl
position_snapshot.json
smb_trader_executions/*.csv
*.pkl
```

### 2. Check What Will Be Committed

```bash
# See what files git is tracking
git status

# See what changes will be included
git diff
```

### 3. Stage Files for Commit

```bash
# Stage all files (respects .gitignore)
git add .

# Or stage specific files
git add smb_screener.py README.md pyproject.toml
git add smbweb/
```

### 4. Make Your First Commit

```bash
# Commit with a descriptive message (avoid service names)
git commit -m "Initial commit: Trading automation bot with IB integration

- Add main position monitoring bot
- Add Django web app for execution tracking
- Add README with project overview
- Configure dependencies and project structure"
```

### 5. Push to GitHub

```bash
# Push to main branch
git push -u origin main

# If your default branch is 'master', use:
git push -u origin master
```

## Writing Good Commit Messages

Good commit messages help you understand changes later:

### Format

```
Short summary (50 chars or less)

More detailed explanation if needed. Can wrap to 72 characters.
Explain what and why, not how (the code shows how).

- Bullet points for multiple changes
- Each bullet describes one logical change
```

### Examples

**Good:**
```
Add trailing stop calculation for position management

Implements trailing stop based on last 3 bars of 15-minute data.
Uses regular trading hours data only. Falls back to ADR-based
stop if trailing stop calculation fails.

- Add calculate_trailing_stop() function
- Integrate trailing stop into NEW/ADD order flow
- Add diagnostic logging for stop price calculation
```

**Bad:**
```
fix stuff
```

**Better:**
```
Fix IB connection handling after computer sleep

Session cookies were invalidated after system sleep, causing
authentication failures. Now detects connection errors and
recreates session automatically.

- Add connection error detection in polling loop
- Force session recreation on ConnectionError
- Add retry logic with 5-second delay
```

## Regular Workflow

### Daily Development Workflow

```bash
# 1. Check current status
git status

# 2. Review changes
git diff

# 3. Stage changes
git add <files>

# 4. Commit with message
git commit -m "Description of changes"

# 5. Push to GitHub
git push
```

### Making Changes Safely

1. **Create a branch** for new features:
   ```bash
   git checkout -b feature/add-options-support
   ```

2. **Make changes** and test locally

3. **Commit frequently** with clear messages:
   ```bash
   git commit -m "Add options detection in normalize_record"
   ```

4. **Push branch** to GitHub:
   ```bash
   git push -u origin feature/add-options-support
   ```

5. **Create Pull Request** on GitHub to merge into main

## Protecting Sensitive Information

### If You Accidentally Committed Secrets

1. **Remove from history** (if not pushed yet):
   ```bash
   git reset HEAD~1  # Undo last commit, keep changes
   # Fix .gitignore, then recommit
   ```

2. **If already pushed**, you need to:
   - Change the credentials immediately
   - Use `git filter-branch` or BFG Repo-Cleaner to remove from history
   - Force push (⚠️ dangerous - coordinate with team)
   - Or create a new repository

### Best Practices

1. **Use environment variables** for all secrets:
   ```python
   # Good
   API_USERNAME = os.getenv("API_USERNAME")
   
   # Bad
   API_USERNAME = "my_username"
   ```

2. **Add to .gitignore immediately** when creating new files with sensitive data

3. **Review before committing**:
   ```bash
   git diff --cached  # See what's staged
   ```

4. **Use a pre-commit hook** to scan for secrets (optional):
   ```bash
   # Install git-secrets or similar tool
   ```

## Documentation Strategy

### What to Document

✅ **Do Document:**
- How to set up and run the bot
- Configuration options and their effects
- API endpoints and general workflow
- Troubleshooting common issues
- Architecture overview (high-level)

❌ **Don't Document:**
- Specific trading strategies
- Proprietary risk management formulas
- Exact position sizing algorithms
- Internal business logic details

### Example: Documenting a Feature

**Good Documentation:**
```markdown
## Trailing Stop Feature

The bot calculates trailing stops based on recent price action to
protect profits. When entering a new position, the bot:

1. Retrieves the last 3 bars of 15-minute data
2. Calculates the lowest low (for longs) or highest high (for shorts)
3. Uses this as the stop loss price

If trailing stop calculation fails, the bot falls back to an
ADR-based stop loss calculation.
```

**Too Detailed (Proprietary):**
```markdown
## Trailing Stop Formula

The exact formula is:
- For longs: min(low_1, low_2, low_3) * 0.9975
- For shorts: max(high_1, high_2, high_3) * 1.0025
- Uses 0.25% buffer to account for slippage
- Only applies during RTH between 9:30-16:00 EST
```

## Branching Strategy

### Recommended Branches

- `main` or `master` - Production-ready code
- `develop` - Integration branch for features
- `feature/*` - New features
- `bugfix/*` - Bug fixes
- `hotfix/*` - Urgent production fixes

### Example Workflow

```bash
# Start new feature
git checkout -b feature/improve-error-handling

# Make changes and commit
git add .
git commit -m "Add retry logic for API calls"

# Push feature branch
git push -u origin feature/improve-error-handling

# Create Pull Request on GitHub, then merge
# After merge, update local main
git checkout main
git pull origin main
```

## Useful Git Commands

```bash
# See commit history
git log --oneline

# See what changed in a file
git log -p smb_screener.py

# Undo uncommitted changes
git checkout -- <file>

# Undo last commit (keep changes)
git reset --soft HEAD~1

# See differences between branches
git diff main..feature-branch

# Create a tag for a release
git tag -a v1.0.0 -m "Initial release"
git push origin v1.0.0
```

## Reducing Discoverability

To minimize the chance of your repository being found via search:

1. **Use Generic Repository Names**: Avoid specific service names in repo title
2. **Private Repository**: Always use private repos for proprietary code
3. **Generic README**: Avoid mentioning specific services or APIs by name
4. **Generic Commit Messages**: Don't include service names in commit messages
5. **No Public Topics/Tags**: Don't add public topics that mention specific services

## Summary Checklist

Before pushing to GitHub:

- [ ] `.env` is in `.gitignore` and not committed
- [ ] Cookie files are in `.gitignore`
- [ ] Position snapshots are in `.gitignore`
- [ ] Execution CSV files are ignored
- [ ] No hardcoded credentials in code
- [ ] Sensitive trading parameters moved to env vars (optional)
- [ ] README is portfolio-focused (not instructional)
- [ ] README avoids specific service names
- [ ] Commit messages avoid service names
- [ ] Code is tested locally
- [ ] Repository is set to **Private**
- [ ] Repository name is generic (not service-specific)

## Getting Help

- **Git Documentation**: https://git-scm.com/doc
- **GitHub Guides**: https://guides.github.com
- **Git Cheat Sheet**: https://education.github.com/git-cheat-sheet-education.pdf
