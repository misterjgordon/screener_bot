# New position alerts (Cursor trigger payloads)

One **JSON file per NEW position** so each commit = one triggerable event for Cursor.

## Folder

`alerts/new_positions/` (repo root; tracked in git).

## File type

`.json` only.

## File naming

One file per event, unique and sortable:

```text
{YYYY-MM-DD}-{HHMMSS}-{underlying}.json
```

Examples:

- `2026-03-05-093012-AAPL.json`
- `2026-03-05-094531-NVDA.json`

Use the **underlying** ticker (equity symbol) so the agent can look up news. If two NEW positions for the same underlying land in the same second, append a suffix (e.g. `-2`).

## JSON structure

Mirror `PositionSummary` (from `trading.models`) so the screener can call `row.to_dict()` and add one field:

| Field             | Type    | Description |
|-------------------|---------|-------------|
| `trader`          | string  | e.g. "Jeff Holden" |
| `is_long_term`    | boolean | LT account |
| `symbol`          | string  | Display symbol |
| `instrument_type` | string  | equity / option |
| `underlying`      | string  | **Use for news lookup** (equity ticker) |
| `expiry`          | string \| null | Option expiry |
| `strike`          | number \| null | Option strike |
| `option_type`     | string \| null | C / P |
| `net_side`        | string  | long / short / flat / conflict |
| `conflict`        | boolean | |
| `total_magnitude` | number  | Position size / weight |
| `prev_magnitude`  | number \| null | |
| `delta_magnitude` | number \| null | |
| `change_type`     | string \| null | NEW for these files |
| `order_placed`    | boolean \| null | |
| **`detected_at`** | string  | **Required.** ISO 8601 UTC when the NEW was detected (e.g. `2026-03-05T14:30:12Z`). |

Example:

```json
{
  "trader": "Jeff Holden",
  "is_long_term": false,
  "symbol": "AAPL",
  "instrument_type": "equity",
  "underlying": "AAPL",
  "expiry": null,
  "strike": null,
  "option_type": null,
  "net_side": "long",
  "conflict": false,
  "total_magnitude": 1.0,
  "prev_magnitude": null,
  "delta_magnitude": 1.0,
  "change_type": "NEW",
  "order_placed": null,
  "detected_at": "2026-03-05T14:30:12Z"
}
```

## Cursor trigger

Configure the automation to run when **files under `alerts/new_positions/`** are added or changed (e.g. on push to the default branch). The agent should:

1. Read the new JSON file(s) from the commit.
2. Use `underlying` (and optionally `symbol`) to look up relevant news.
3. Summarize and post to Slack (or Discord via a follow-up step).
