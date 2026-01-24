# PostgreSQL Setup for Trading Project

## Current Status

- **PostgreSQL Version**: 16.11_1 (installed via Homebrew)
- **Service Status**: Running
- **Database Created**: `database_smb`
- **PostgreSQL Location**: `/opt/homebrew/opt/postgresql@16/`

## PostgreSQL Installation Confirmation

PostgreSQL is **installed and running**. The service is managed by Homebrew and is currently active.

## Database Information

- **Database Name**: `database_smb`
- **Owner**: `joel`
- **Encoding**: UTF8
- **Locale**: en_US.UTF-8

## Accessing PostgreSQL

✅ **PostgreSQL has been added to your PATH!** You can now use commands directly.

### Using PostgreSQL Commands

Now that PostgreSQL is in your PATH, you can use commands directly:

```bash
# Connect to database
psql database_smb

# List all databases
psql -l

# Create a new database (if needed)
createdb database_name

# Drop a database (if needed)
dropdb database_name
```

### What Was Added to PATH

The following was added to your `~/.zshrc` file:

```bash
# PostgreSQL 16
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
```

This makes PostgreSQL commands available in all new terminal sessions. If you're in an existing terminal, run:
```bash
source ~/.zshrc
```

Or simply open a new terminal window.

## Verify Database Connection

Test the connection:
```bash
psql database_smb
```

Once connected, you can run SQL commands:
```sql
-- List all tables (will be empty until Django migrations are run)
\dt

-- Show database info
\l database_smb

-- Exit
\q
```

## PostgreSQL Service Management

```bash
# Check status
brew services list | grep postgresql

# Start service (if stopped)
brew services start postgresql@16

# Stop service
brew services stop postgresql@16

# Restart service
brew services restart postgresql@16
```

## Database Connection Details for Django

When configuring Django settings, use these values:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'database_smb',
        'USER': 'joel',  # Your macOS username
        'PASSWORD': '',  # Usually empty for local development
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

Or in `.env` file:
```
DB_NAME=database_smb
DB_USER=joel
DB_PASSWORD=
DB_HOST=localhost
DB_PORT=5432
```

## Next Steps

1. ✅ PostgreSQL installed and running
2. ✅ Database `database_smb` created
3. ⏭️ Install Django and psycopg dependencies
4. ⏭️ Create Django project structure (mirroring jambot)
5. ⏭️ Configure Django settings
6. ⏭️ Run migrations to create tables

## Troubleshooting

### "psql: command not found"
- Reload your shell: `source ~/.zshrc`
- Or open a new terminal window
- Verify PATH: `echo $PATH | grep postgresql`

### "could not connect to server"
- Check if PostgreSQL is running: `brew services list | grep postgresql`
- Start it: `brew services start postgresql@16`

### "database does not exist"
- List databases: `psql -l`
- Create database: `createdb database_smb`

### "password authentication failed"
- For local development, PostgreSQL usually doesn't require a password
- If it does, check your `~/.pgpass` file or PostgreSQL configuration
