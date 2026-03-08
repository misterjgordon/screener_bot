# Alerts (Cursor / automation trigger)

Alerts written here are **tracked in git** so Cursor Automations (or other tools) can trigger on push/commit.

- **`new_positions/`** – one JSON file per SMB NEW position. Each new file + commit can start a Cursor Cloud Agent to look up news and post a summary to Slack/Discord.

Do not use `resources/positions/` for trigger payloads; that directory is gitignored.
