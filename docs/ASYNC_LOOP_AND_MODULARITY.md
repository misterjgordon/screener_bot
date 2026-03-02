# Separating functionality without asyncio/ib_async loop issues

## Why the loop causes trouble

- **ib_async** uses asyncio. It gets the event loop via `getLoop()`: first `get_running_loop()`, then the thread’s loop from the policy, or it creates a new one and sets it.
- If **two modules** (or the same module in different entry points) call `asyncio.set_event_loop(asyncio.new_event_loop())` or otherwise create/set a loop, you get:
  - Different loops in different threads or at different times.
  - “Client id already in use” when the same process reconnects with a different loop or after the policy was changed.
  - Timeouts / cancelled futures when work is scheduled on a loop that isn’t the one ib_async is using.

So splitting the script into parts is safe only if **everyone shares a single event loop** and **only one place** creates/sets it.

---

## Solutions (pick one and stick to it)

### 1. Single event loop at one entry point (minimal change)

**Rule:** Exactly one module is the “owner” of the event loop and the only place that touches it. All other code uses IB by receiving an `ib` instance and never calls `asyncio.set_event_loop` / `new_event_loop`.

- **Entry point:** The script you actually run (e.g. `trading.smb_screener` when started by launchd). At the very top, before any other imports that use ib_async:
  - Set the loop once: `asyncio.set_event_loop(asyncio.new_event_loop())`
  - Then: `from ib_async import ...`
  - Then: import your other modules (e.g. `trading.market_data`, `trading.config`).
- **Other modules (e.g. `market_data`):**
  - Do **not** call `set_event_loop` or `new_event_loop` at module level.
  - When run as `__main__` (e.g. `python -m trading.market_data SYMBOL`), they are a **different** process, so they can set their own loop in `if __name__ == '__main__':` only. Do **not** set the loop at module level, or the same process that imports `market_data` (e.g. the screener) could get a different loop when it later imports this module in another context.
- **Practical fix:** In `market_data.py`, keep `asyncio.set_event_loop(asyncio.new_event_loop())` only inside `if __name__ == '__main__':` (so it runs only when you run that file as the script). When the screener does `from trading.market_data import ...`, that block is not executed, so the screener’s loop (set in `smb_screener`) remains the only one.

**Import order from the entry point must be:**

1. `asyncio` + set loop  
2. `ib_async`  
3. Everything else that imports ib_async or code that uses it (config, market_data, etc.)

---

### 2. Async-native entry point (recommended by ib_async maintainer)

Use one event loop for the whole process, started with `asyncio.run()`, and do all IB work inside that loop (optionally with `connectAsync` and async helpers).

- **Single entry point**, e.g.:

```python
# run_screener.py or smb_screener main
import asyncio
from ib_async import IB

async def main():
    ib = IB()
    await ib.connectAsync(host, port, clientId=...)
    # Now run your polling loop with asyncio.sleep(interval), not time.sleep()
    # and call your sync helpers (get_market_price(ib, sym), etc.) from here.
    # Those helpers use ib.run() / ib.sleep() which use the same loop.
    while True:
        run_single_cycle(ib)  # or inline the logic
        await asyncio.sleep(INTERVAL_SECONDS)

if __name__ == '__main__':
    asyncio.run(main())
```

- **No** `set_event_loop` anywhere. The loop is created and owned by `asyncio.run()`.
- **Other modules** stay as they are: they receive `ib` and use it (e.g. `get_market_price(ib, symbol)`). They never create a loop; `ib.run()` / `util.run()` use `get_running_loop()` inside `asyncio.run(main())`, so there’s no conflict.
- Use **async I/O** (e.g. `aiohttp`/`httpx`) for SMB API calls if you do them inside the same process, so you don’t block the loop. Alternatively run the HTTP part in a thread/executor and only use the main thread for IB.

This avoids “who sets the loop?” entirely and matches [ib_async’s recommended pattern](https://github.com/ib-api-reloaded/ib_async/discussions/36).

---

### 3. Separate process for IB (or for “extra” functionality)

If you want a dedicated process for market data or a second client:

- Run that script in a **separate process** (e.g. `python -m trading.market_data` or a small “IB bridge” process). Each process has its own event loop and its own `IB()` connection (use a **different** `clientId` per process).
- Communicate between processes via queues, sockets, or HTTP. The main screener then doesn’t import ib_async in the same process as the market-data script, so no shared-loop issues.

Use this when you intentionally want multiple IB clients or to isolate crashes; it’s more moving parts than you need if you only want to split code into modules.

---

### 4. Explicit loop passing (advanced)

- Create the loop in the main module and pass it (or an “app context”) into code that needs it. ib_async’s `getLoop()` uses the thread’s event loop policy; if you never call `set_event_loop` elsewhere and all code runs on the same thread, the loop you set at startup is the one that gets used. So “explicit passing” here really means: **document that the main script sets the loop once**, and no other module is allowed to change it. That’s the same as solution 1.

---

## Summary

| Approach              | Effort   | Robustness | Best for                          |
|-----------------------|----------|------------|------------------------------------|
| 1. Single loop owner  | Low      | Good      | Current layout, minimal refactor   |
| 2. Async-native run() | Medium   | Best      | New or refactored entry point      |
| 3. Separate process   | Higher   | Isolated  | Multiple clients / isolation       |

**Recommended:** Use **1** immediately (one entry point sets the loop once; `market_data` only sets a loop in `if __name__ == '__main__'`). Plan for **2** when you’re ready to make the main loop async-native so all functionality can be split into modules without touching the event loop outside the single `asyncio.run(main())` entry point.
