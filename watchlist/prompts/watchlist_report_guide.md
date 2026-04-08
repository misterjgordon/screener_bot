# Watchlist report — Claude summarization guide

Purpose: to create a structured market and stock summary for a **US day trader** who wants to **capitalize on directional, outsized moves** in individual names.
**Audience:** Active intraday trader, not a long-form macro newsletter reader. Prefer **actionable**, **specific** over generic filler.

**Output:** One coherent report with the sections below. Use clear headings exactly as numbered. If data for a section is missing, say **Insufficient data** and list what would be needed—do not invent catalysts or tickers. Provide a 

Use **today’s ingested sources** (SMB gameplan → structural list + narrative in JSON payload. Trader_tv →  macro commentary and stock movers/, tickers_on_watchlist_*.json → union of symbols + atr_14 + percent_of_avg_volume + gap_percent + gap_atr) from todays date watchlist/repository/YYYY/MM/DD/ on the current day into a market commentary and stock summary report usign the 6 sections below. Based on the understanding of the marck and specific stock news, we know "where the stocks have moved from" we want a best estimate of "which are most likely to move the most in the direction of our bias".

---

## Section 1 — Macro

**Goal:** Describe the **current macro environment** as it matters for **risk-on/risk-off**, rates, USD, commodities, and **major scheduled risks** (CPI, FOMC, geopolitical headlines) *when those appear in the sources*.

**Required:**

- **Current tone:** One tight paragraph (e.g. risk-on / defensive / mixed) with **why**, tied to evidence in the sources.
- **Notable changes vs prior trading day:** Bullet list of **what changed** (sentiment, levels, narrative) compared to **yesterday’s** macro picture **only if the sources allow**; otherwise state that prior-day context was not in the bundle.

**Avoid:** Generic macro essays unrelated to today’s tape unless the sources emphasize them.

---

## Section 2 — Movers

**Goal:** Highlight names that qualify as **potential outsized directional movers today**, using **strict inclusion rules** below. This is not a full market scan—only what the **provided sources** justify.

### Core inclusion criteria (apply in order)

#### a) Catalyst(s)

A stock may not have a news catalyst, it may have an upcming announcement or event, verify this if now news can be determined.

**i) Earnings**

- EPS / revenue beat or miss
- Guidance changes
- Margins, bookings, or segment surprises

**ii) Non-earnings**

- Upcoming events or announcements
- M&A (acquisition, divestiture, stake)
- Analyst actions (**only if impactful** and sourced)
- Sector/theme-driven news (AI, commodities, software, etc.)
- Capital markets activity (offering, buyback, debt, etc.)


**iii) US government (priority when present)**
- Trump announcements/tweets/etc.
- FDA decisions
- DoD / federal contracts
- Tariffs, legislation, rulings
- Regulatory approvals / rejections
#### b) Price movement

- Must show **meaningful extended-hours** price movement **as described in the sources** (or clearly implied with evidence).
- Move should be **outside normal ATR behavior** *when ATR or range context is available*; if not available, say so and rely on **% change vs typical** only if the source provides comparables.
- Typically: large **%** change **relative to its usual range**, and/or **abnormal volatility expansion** when data supports it.

#### c) Time window (strict)

- **Morning report:** From **prior regular-session close** through **6:20 a.m. PT** (pre-market window for this report).


#### d) Surprise classification (mandatory per included name)

For **each** included ticker, label catalyst surprise as one of:

| Label    | Meaning |
|----------|---------|
| Bullish | Better than expected / bullish development |
| Bearish | Miss, downgrade, adverse event |
| Mixed    | Conflicting signals (e.g. revenue beat + EPS miss) |
| Neutral  | Flow-driven or catalyst unclear |


#### 6) Relevance filter

**Include** only if:

- A trader could **realistically act** on it today (timing and catalyst still relevant).
- **Repeatable catalyst logic** (cause → effect is explainable).
- **Clean** cause → effect in the sources.

**Exclude:**

- Pure noise / no-news movers
- Low-quality speculation **without** a catalyst in the sources
- Redundant names with **identical drivers** unless one is **notably** larger/more liquid/more actionable—then keep the best one and mention the others briefly.

**Output format for Section 2:**

- Table or bullet list: **Ticker — Catalyst — Surprise — Why it matters today — Key risk / invalidation**
- If **no names** qualify, say so explicitly.

---

## Section 3 — Second day

**Goal:** Names from **day 1** (the prior completed regular session) that qualify for **day 2** follow-through, using **definitions below**. Prefer **precomputed metrics** passed into the prompt (see *ATR and metrics*, below)—do not guess ATR from prose.

**Criteria (all should hold when data is available):**

1. **ATR multiple:** Day 1’s range (or move vs prior close—match whatever the pipeline computes) relative to **14-day ATR** is **> 2**. ATR must come from **real OHLC** (e.g. IB-backed daily bars), not from the LLM.
2. **Close location vs day 1 range (per stock):** Judge **only that symbol’s** daily bar for day 1:
   - **Bullish day-1 close:** Regular-session **close** in the **top 30%** of that day’s **high–low range** → candidate for **long-bias** day 2 continuation.
   - **Bearish day-1 close:** Close in the **bottom 30%** of that day’s range → candidate for **short-bias** day 2 continuation.

**If ATR/range metrics are not in the prompt bundle:** output **Insufficient data** for Section 3 and list what file/field is missing.

---

## Section 4 — Themes

**Goal:** 3–7 **thematic buckets** (e.g. Iran conflict, AI and software stocks, precious metals, AI capex, biotech PDUFA week, regional banks, energy geopolitics) that tie **multiple tickers** or macro drivers together **as supported by the sources**.

For each theme: **one-line thesis**, **what stocks/sector benefits / who is hurt**, what would flip the narrative today.

---

## Section 5 — Week ahead

**Goal:** Forward-looking **calendar and event risk** for the **next 5 trading days** *when available in sources*. Include: macro prints, Fed speakers (if relevant), major earnings clusters, FDA dates, etc.

If the bundle lacks a calendar: **Insufficient data**.

---

## Section 6 — Ranking

**Goal:** Rank the **top trading ideas for today** (from Sections 2–4), not every mentioned. Each of the ranking dimensions have points associated with them, and the sum of the points is the total rank.

**Ranking dimensions:**

1. **Catalyst** (0-40) (impact of news on the value of the stock, ex. it completely changes the company's valuation from $100M to $200M would be a 40/40 point score)
2. **After-hours + pre-market move** (0-30)(30 would equal a 1 ATR move from the prior day's close, so a 2 ATR move would also be 30)
3. **Market cap** (0-10) (small cap stocks are 0-10, mid cap stocks are 10-20, large cap stocks are 20-30)
4. **Short Interest*** (0-5)
5. **Precent of average volume** (0-5) - if the percent_of_avg_volume value in the json file is < 10=0 point, 10-15 = 2 points, 15-25 = 3 points, 25-35 = 4 points, > 35 = 5 points
6. **Technical position** (0-10) - unclear what the qualifications are yet so for now this will be left blank

---

## Section 7 — Performance (placeholder)

**Goal (future):** Compare **prior rankings** to **realized intraday behavior**: which ideas moved in the suggested direction, magnitude vs expectation, **false negatives** (big movers not suggested).

**Rankings storage:** Persist Section 6 output as **one file per desk day** under the same day folder (e.g. `watchlist/repository/YYYY/MM/DD/ranking_YYYY-MM-DD.json` or similar). Section 7 reads **yesterday’s** ranking file vs today’s outcomes.

**For now:** Write **Not evaluated — historical outcomes not yet wired** unless yesterday’s ranking file and realized data are supplied.

---

## ATR and metrics

- **ATR belongs in Python**, using **`strategies/indicators/atr.py`** (or equivalent) on **real daily bars** from **IB** (or your existing bar loader), same as the rest of the trading stack.
- **Pre-market / after-hours moves** vs prior close: compute from **last regular close** and **latest extended-hours quote or bar** from IB when you wire it—still **code**, not the model.
- Pass results into the report as **structured JSON** **in addition to** narrative sources. That keeps **token use low** and **numbers trustworthy**.


---

## Universe and reports (resolved)

- **Reports:** **One** morning-oriented report for now (prior close → 6:00 a.m. PT window for movers in Section 2).
- **Universe:** Default to **US common equities**; **include ADRs and ETFs** when sources/context justify them (e.g. metals in play → `SLV`, `GOLD` / `GLD`-style names as listed).

---

## Global rules

2. **Provenance:** Where useful, note **which file or excerpt** supports a claim (filename is enough).
3. **Bias toward clarity:** Short sentences, trader vocabulary, minimal adjectives.
4. **Risk:** Remind once that this is **not investment advice** and that liquidity/news can change instantly.

---

## Resolved operator decisions

1. **ATR / PM-AH moves:** Computed in **Python** from **IB (or existing bar path)** + `atr.py`; feed structured numbers into the prompt. 
2. **Section 3 bullish/bearish:** **Per-stock** day-1 daily bar: close in **top 30%** of range = bullish; **bottom 30%** = bearish; combined with **ATR multiple > 2**.
3. **Reports:** **Single** report for now.
4. **Universe:** US equities default; **ADRs/ETFs** allowed when context warrants (e.g. metals).
5. **Rankings:** **One file per day** in the repository (same `YYYY/MM/DD` tree) for Section 6 / Section 7 handoff.
