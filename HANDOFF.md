# Card Arbitrage Scanner — Full Context Handoff

You are picking up an in-progress project for a user named **Andrew**. Read
this ENTIRE document before touching anything. It is the single source of
truth: the goal, the architecture, every file, every config knob, what
works, what is broken, the conventions you must follow, and the full history
of what has been built. Do not re-derive or rebuild things that already
exist — extend them. This doc supersedes all prior handoff notes.

Last updated: 2026-07-25 (end of the bot-block + valuation-overhaul
session; see §7 items 17-22 for that day's work and §8 for live state).

---

## 1. Who Andrew is and what he wants

Andrew is a former ETF trader deploying hundreds of thousands of dollars of
working capital into buying and reselling collectibles for profit —
primarily graded Pokemon cards, plus sports cards, sealed vintage video
games, watches, and pop-culture/comics. He also has a **personal
collection** he is actively hunting ("grails").

He wants a system that:
- Scrapes auction + fixed-price (BIN) listings across multiple marketplaces
- Values each item with a sophisticated, self-improving algorithm on real
  market data
- Surfaces the best PROFIT opportunities (biggest expected profit / ROI /
  capital velocity) as a sorted Excel report
- Separately surfaces GRAILS — cards significant to his personal collection —
  regardless of profit
- Sends Telegram alerts + a daily Telegram digest
- Runs automatically on a schedule
- Improves its own valuations over time from observed outcomes

He is NOT a developer. Explain things plainly, be concise (he values
brevity), and never make him read code. He interacts by double-clicking
`.command` files and editing `config.yaml`. His mental models are a trader's:
edge, ROI, capital velocity, mark-to-market, IRR. Lean into that framing.

His personality: sharp, fast, catches real bugs by eyeballing output. When
he pastes report rows and says "why is this here," he's usually right that
something is wrong. Take it seriously and fix the root cause.

---

## 2. Where everything lives

Everything is in `~/Desktop/ebay opportunities/` on his Mac:

```
ebay opportunities/
  Run Scan.command          # double-click: full scan -> "Opp Runs/" (pip-syncs deps, caffeinated)
  Run BIN Sweep.command     # double-click: fast BIN sweep -> "BIN runs/" (caffeinated)
  Setup Telegram.command    # one-time telegram chat-id finder
  Test Comps.command        # live test of 130point + eBay sold comps; tees to comps_test_result.txt
  Test Alerts.command       # sends a TEST telegram via the real code path
  Fix Mac Sleep.command     # one-click sudo pmset -c sleep 0 (Andrew declined - handles sleep his way)
  comps_test_result.txt     # last Test Comps output (readable from sandbox)
  .cookies_ebay.json etc.   # persistent per-site cookie jars (auto-managed)
  .scan.lock                # single-run lockfile (auto-managed)
  config.yaml               # ALL settings + API keys (the only file he edits)
  portfolio.csv             # NEW: his positions ledger (he edits in Excel)
  history.db                # SQLite: comps, guide cache, calibration, alerts
  scan.log                  # NEW: rotating run log (evidence of every scan)
  learned_params.json       # learner output (settle ratios, ML status)
  model.pkl                 # learner ML model (only when deployed)
  requirements.txt
  FEATURES.md, HANDOFF.md   # docs (this file)
  .venv/                    # python env (auto-created on first run)
  Opp Runs/                 # dated full-scan xlsx outputs
  BIN runs/                 # dated BIN-sweep xlsx outputs
  code/
    main.py                 # orchestrator + CLI + 3-phase parallel pipeline
    models.py               # Listing, SoldComp, Valuation, Opportunity dataclasses
    report.py               # Excel generation (all tabs, formatting)
    alerts.py               # Telegram + macOS alert gating/delivery
    digest.py               # NEW: post-scan top-25 profit + top-25 grail digest
    grails.py               # NEW: personal-collection grail matching/scoring
    closer.py               # NEW: auction close-tracker (real final prices)
    portfolio.py            # NEW: inventory & P&L mark-to-market
    db.py                   # SQLite helpers + schema
    learner.py              # self-improving settle-ratio / ML close model
    misspell.py             # typo-variant query generator
    demo_data.py            # synthetic data for --demo
    scrapers/
      base.py               # HTTP, politeness, retries, per-channel breaker, parallel lock
      ebay.py               # eBay Browse API + HTML fallback (the workhorse)
      yahoo_jp.py           # Japan via Buyee (REWRITTEN, works)
      point130.py           # 130point sold comps (REWRITTEN, works)
      fanatics_collect.py   # Fanatics via Algolia (BLOCKED - key rotated)
      goldin.py, heritage.py# placeholder/dead, not in active sites list
    valuation/
      comps.py              # comp matching, conflict filters, velocity, subject guard
      price_guide.py        # PriceCharting + pokemontcg.io
      engine.py             # the valuation model (fair value, EV, capture, score)
```

**MOUNT QUIRK (critical):** In the assistant's sandbox the folder is also
reachable, but SQLite WRITES to `history.db` from the sandbox throw "disk
I/O error" (FUSE limitation) AND the sandbox proxy blocks most external
hosts (eBay, 130point, Telegram, Buyee all return 403 from bash/python).
So: (a) do NOT modify history.db from bash; put DB migration logic in the
code which runs natively on his Mac. (b) You CANNOT hit live sites from the
sandbox — use the Claude-in-Chrome browser tools to inspect/verify live
sites, and use `--demo` + synthetic unit tests to verify code. Telegram
sends work from Chrome's `javascript_tool` (fetch to api.telegram.org) but
not from sandbox bash.

**Two copies of the code:** the running copy is `~/Desktop/ebay
opportunities/code/`. There is also a scratch copy at the assistant's
outputs dir used for testing. ALWAYS edit the real copy under the Desktop
folder; when testing in the sandbox, `cp` the files to `/tmp/oppo/` (a full
working copy is kept there) and run there. Keep them in sync.

---

## 3. How to run / test

- **Full pipeline test, no network/keys:** from the folder root run
  `python3 code/main.py --demo -o /tmp/test.xlsx`. Uses synthetic data.
  This is your primary regression check — run it after EVERY change.
- **Calibration status:** `python3 code/main.py --calibrate`
- **CLI:** `--mode all|auctions|bin`, `--demo`, `--calibrate`, `-o OUTPUT`,
  `-c CONFIG`, `-v` (verbose/DEBUG).
- The `.command` scripts create/refresh `.venv` from requirements.txt, cd to
  the folder root, and call `code/main.py`.
- **Sandbox testing pattern (what the assistant has been doing):**
  `rm -rf /tmp/oppo && mkdir -p /tmp/oppo && cp -r "<folder>/code" "<folder>/config.yaml" "<folder>/requirements.txt" /tmp/oppo/`
  then `cd /tmp/oppo && python3 -m py_compile <files> && python3 code/main.py --demo -o /tmp/t.xlsx`
  plus targeted `unittest.mock`-based unit tests for each new function.
  `pip install --break-system-packages openpyxl pyyaml beautifulsoup4 requests`
  in the sandbox as needed. scikit-learn AND curl_cffi are NOT installable
  in sandbox (fine — ML tier gated; curl_cffi falls back to plain requests
  with a logged warning, and its code path is unit-tested via a stubbed
  module — see §7.17 tests for the pattern).
- **SQLite from sandbox: reads used to work via
  `sqlite3.connect("file:history.db?mode=ro", uri=True)` but as of
  2026-07-25 even read-only opens can throw "attempt to write a readonly
  database" over FUSE.** Ground truth then comes from scan.log and the
  report xlsx (openpyxl reads work fine).
- **Cron on his Mac (already installed):** full scan daily at 6pm, BIN sweep
  every 30 min. Overlap is now safe (.scan.lock: sweeps skip, full scans
  wait 15 min). Both .command scripts run under `caffeinate -im`. Mac
  sleep: Andrew declined the pmset fix ("ill handle sleep the way i want")
  — if sweeps go missing overnight, that's the first suspect.

---

## 4. Conventions you MUST follow

- **Never handle his API keys/tokens directly.** He pastes them into
  config.yaml himself. If he asks you to paste a key, decline and give steps.
  (The Chrome extension also redacts credential-looking strings from you —
  that's expected; work around it by splitting strings in half, etc.)
- **After ANY programmatic config edit, verify keys are still present**
  (`python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); assert
  c['api_keys']['ebay']['client_id'] and c['alerts']['telegram']['bot_token']"`).
  He has occasionally hand-edited config between sessions (added watchlist
  entries, fixed spellings) — re-read it, don't assume.
- **Test every change with `--demo` before telling him it's done.** Add
  targeted unit tests for new logic. Prefer grounded evidence (log/db/report
  analysis, live Chrome inspection) over guessing.
- Keep a task list updated (TaskCreate/TaskUpdate) — it renders as a widget.
- Use the xlsx/docx skills for deliverables when relevant.
- Explain outcomes in plain language, concise. Lead with the outcome.
- When he reports a bad row in the output, reproduce it as a unit test case
  from his exact pasted title/price, fix the root cause, and prove it with
  that test.

---

## 5. Data model (models.py)

`Listing` fields: site, title, url, current_price, shipping, bid_count,
end_time, image_url, listing_id, query, priority (bool), discovery (bool),
misspell_from (str), listing_type ("auction"|"fixed"), best_offer (bool),
**has_buy_now** (bool — auction that ALSO has a BIN), **grail** (str, matched
grail name), **grail_score** (float 40-100), created_at, currency,
marketplace, seller_feedback. Properties: `age_hours`, `total_cost_now`
(price+shipping), `hours_remaining`.

`SoldComp`: title, price (incl shipping if known), sold_date, url, site.

`Valuation`: fair_value, comps_value, guide_value, n_comps, dispersion,
confidence (0-1), expected_cost, expected_value, edge_now, capture (0-1),
trend_30d, roi, **sales_per_month**, **annualized_roi**, opportunity_score,
**regraded** (bool — fair value recomputed at the LISTING's grade/subject,
kept out of fair_history), **disputed** (bool — comps vs guide >4x apart,
conf capped 0.30, kept out of fair_history + Crossover + Today),
notes (list[str], human/decision), **audit_notes** (list[str], model
diagnostics — rendered in the hidden "Model Detail" column).

`Opportunity`: listing + valuation. Report may attach a **dupe_note** attr
("+N more listing(s) from $X") when duplicate listings were collapsed.

---

## 6. The valuation model (valuation/engine.py + comps.py)

### Fair value (engine.fair_value)
Blend of sold-comps value and price-guide value. Comps weight grows with
sample size (saturates ~8 comps), shrinks with dispersion, floored at 0.35
so comps (truer signal) dominate when present. Fallbacks in order:
comps+guide blend → comps only → guide only (×0.95 staleness haircut) →
**ask-based** (25th percentile of live fixed-price asks × 0.9, flagged
"ASK-BASED ... verify", confidence capped ≤0.35). If nothing: fair_value 0.

### Comp hygiene (comps.robust_comp_value)
Fuzzy title match (min 0.55), MAD outlier trim (k=3), recency-weighted
median (30-day half-life). A comp is EXCLUDED on any of:
- `grade_conflict` — different effective grade (see grade normalization)
- `language_conflict` — foreign-language versions (German/French/Spanish/
  Italian/Japanese incl. native names Glurak/Dracaufeu/etc.). JP-native
  queries (topsun, carddass, no rarity, japanese) are exempt.
- `variant_conflict` — holo vs non-holo vs reverse; 1st edition vs unlimited
  vs shadowless; **Topsun original vs Topsun VS** (see §7 history). These are
  DIFFERENT cards at very different prices.
- `subject_missing` (NEW) — the query's SUBJECT tokens (player/character
  name, after stripping ~120 GENERIC_TOKENS of set/grade/format context +
  ordinal fragments) must appear in the title. Kills "Magneton matched to a
  Gengar query" — high context-match, wrong card. All-context queries (e.g.
  "1999 1st Edition Pokemon Set") skip the check. Applied to BOTH listings
  (main.py relevance filter) and comps (comps.py).

### Grade normalization
Every non-PSA grader (CGC/BGS/SGC/BVG) counts ONE full point LOWER than PSA.
A CGC 10 == PSA 9 for matching and valuation. Andrew's explicit rule. Price
multipliers all 1.0 now (the shift does the work). Config
`algorithm.grader_grade_shift` / `grader_premiums`.

**Ungraded = assumed PSA 5 (2026-07-18, Andrew's rule, fixed a major bug).**
Previously grade_conflict PASSED whenever either side lacked a grade token:
raw listings under a "PSA 9" query inherited the PSA 9 fair value (fake
edges), and graded comps inflated raw-query fair values. Now, three layers:
(1) COMP matching (`grade_conflict(..., assume_ungraded=True)`, used by
robust_comp_value + comp_velocity): a title with no grade counts as
effective PSA 5 (`comps.UNGRADED_GRADE`, config `algorithm.ungraded_grade`)
— raw comps only price raw/PSA-5-equivalent queries. (2) LISTING valuation
(`engine._valuation_query`): when a listing's effective grade differs from
its query's, fair value is recomputed at the LISTING's grade against the
same comp pool (grade token swapped into the query; ask-based fallback
disabled since the ask pool belongs to the query's grade). `v.regraded` =
True + an explicit note ("UNGRADED listing - valued as PSA 5 equivalent" /
"valued at listing grade CGC 8"). Regraded fair values are EXCLUDED from
fair_history/trend (main.py gates on `not v.regraded`) so per-listing
regrades can't pollute the query's price history or portfolio marks.
LISTING SURFACING stays permissive (default grade_conflict unchanged in
main.py's relevance filter): a broad ungraded query like "Pikachu
Illustrator" still surfaces PSA 7 listings — they're just VALUED at PSA 7
now. (3) PRICE GUIDE: grades <7 map to loose-price (raw) instead of
silently defaulting to the Grade-9 field. Applies to full scans AND BIN
sweeps (shared engine).

**Set-wide subject injection + disputed values (2026-07-25, §7.19).**
`engine._valuation_query` has a SECOND trigger: when the query has no
subject tokens (set/theme query), the listing's own subject (top-3 longest
alphabetic non-generic title tokens) is injected into the valuation query
— a Mewtwo under "Pokemon No Rarity Set" is valued on Mewtwo sales, not
the set-wide mongrel median. And when comps vs guide disagree >4x
(`agreement < 0.25`), `v.disputed=True`: confidence capped 0.30, "VALUE
DISPUTED" note, excluded from fair_history/Crossover/Today. Also:
effective grades floor at 1 (no "PSA 0"), and graded Pokemon at effective
<= `filters.pokemon_grade_floor` (3) are dropped from output entirely
(Andrew: "I will just never care"; sports/other exempt, grails exempt,
raw exempt).

### Expected cost / close model (engine.score, auction branch) — see §7
- **BINs:** cost = asking price; capture decays with LISTING AGE (fresh=good,
  freshness_half_life_hours=24).
- **Auctions (bid-aware, NEW):** expected final price interpolates between a
  bid-trajectory base and a settle anchor (resale × settle_ratio, default
  0.92). Two bid-aware adjustments:
  - **proxy premium:** displayed bid understates the leader's hidden proxy
    max, so the interpolation base is lifted `proxy_bid_per_bid` (0.02) per
    bid, capped `proxy_bid_cap` (0.15).
  - **sniper floor:** within `late_auction_hours` (6), expected close is
    floored at `sniper_floor` (0.75) × settle — a real card never closes at
    a token bid in a hard-close market. THIS killed the fake late-auction
    edges that were the worst alert false-positives.
  - **price-band settle ratios:** once the learner has ≥20 real closes in a
    band (<$500 / $500-2000 / ≥$2000), that band gets its own settle ratio
    via `ClosePredictor.settle_ratio_for(fair)`; else global learned ratio;
    else config default.
  - **ML override:** if learner deployed a model, `predict_ratio` overrides.

### Buy-side cost / tax / vault (engine, Andrew's exact rules)
- `sales_tax_rate` (0.08) applies to eBay checkout cost + edge/EV.
- `tax_free_marketplaces` [YAHOO_JP, PAYPAY_JP] exempt (exports).
- **PSA vault routing:** eBay items priced ≥ `psa_vault.min_price` ($500)
  are bought into the PSA vault: NO sales tax in, and the exit is a
  consigned sale that nets 93% — `psa_vault.sell_fee_rate` (0.07) is the
  ALL-IN sell-side cost that REPLACES the normal 13.25% marketplace fee
  (not stacked). Under $500: normal checkout, 8% tax in, 13.25% fee out.
  Routing is scenario-aware: `edge_now` routes at the CURRENT price; EV
  routes at the pre-tax EXPECTED close (a $100 bid closing near $2k is a
  vault purchase). Notes column shows which regime applied.

### Expected value / ROI / velocity / score
- proceeds = resale × (1 − sell_fee[vault or normal]).
- expected_value = proceeds − expected_cost (incl tax if applicable).
- roi = expected_value / expected_cost.
- **sales_per_month** (NEW, comps.comp_velocity): liquidity. 130point comps
  have real dates → count last-45-days / 1.5; eBay comps are fetch-stamped
  over eBay's ~90d sold window → matched-count / 3.
- **annualized_roi** (NEW) = roi × 365 / cycle_days, cycle = 30/spm clamped
  7-365d. Andrew's ETF instinct: a 15% flip clearing in 2 weeks beats a 40%
  flip taking a year. NOTE: NOT yet folded into opportunity_score — he may
  want that once he trusts the Sales/mo numbers.
- **opportunity_score** = min(roi,1.0) × confidence × capture. The sort key.

### Scam / data defenses
- `exclude_keywords` (reprint/proxy/replica/custom/digital/orica/etc).
- price < `too_good_ratio` (0.35) × resale → capture ×0.2 + "SUSPICIOUS".
- seller feedback < `flag_seller_feedback_below` (10) → capture ×0.7.
- `filters.max_roi` (2.0) drops absurd-ROI rows as bad data everywhere.

---

## 7. Build history — what was done and WHY (chronological)

This is the detailed record. Every item was tested (`--demo` + unit tests)
and shipped. Keys verified intact after each.

1. **130point scraper REWRITTEN (works).** The site was redesigned mid-2026;
   the old `back.130point.com/sales/` POST endpoint is DEAD. New path (found
   live via Chrome): `GET 130point.com/api/search/html?q=...&saleType=sold`
   returns an HTML fragment of `a[data-sold-result]` cards. Parse:
   `data-price-amount` = actual sale price incl. accepted Best Offers (NOT
   `data-original-price-amount`, the crossed-out ask); `data-price-currency`
   (USD only kept); `data-result-end-time` (ISO date); href = listing URL.
   Covers eBay + Fanatics Collect + more. Verified live via Chrome. Sandbox
   can't reach it — Test Comps.command exists so Andrew can verify from his
   Mac.

2. **eBay sold comps RE-ENABLED + bot-block detection.** `use_html_comps`
   was paused via a stale `html_comps_resume` timestamp; removed. Added
   challenge-page detection in ebay.py `_search_html`: eBay bot-blocks
   return HTTP 200 challenge pages that previously parsed as "0 results"
   silently — now they count as html-lane failures so the circuit breaker
   trips instead of hammering eBay. Same detection reused in closer.py.

3. **Parallel scanning (~4x).** run_live restructured into 3 phases:
   (A) main-thread DB reads, (B) parallel network fetch via
   ThreadPoolExecutor (`scraping.parallel_queries`, default 6),
   (C) main-thread DB writes + valuation. Politeness preserved: BaseScraper
   HTML lane is serialized by a per-instance lock with unchanged delays;
   API lane stays concurrent. Per-site delay overrides via
   `scraping.site_delays` (130point: 1.5). SQLite never touched from worker
   threads. One bad query can't kill a run (try/except per query).

4. **Sales-tax + PSA-vault cost model** (see §6). Evolved across 3 messages
   as Andrew refined it: first flat 8% tax; then vault routing ≥$500 with a
   5% consignment stacked; then corrected to the final rule — vault nets 93%
   flat (7% all-in replaces the marketplace fee, $0 tax in).

5. **Yahoo Japan FIXED via Buyee (works).** Root cause: auctions.yahoo.co.jp
   serves EMPTY pages to non-browser HTTP clients (confirmed: browser gets
   53 products, plain client gets nothing) — the parser was never the
   problem. Rewrote yahoo_jp.py to scrape `buyee.jp/item/search/query/<q>?
   translationType=98` instead: server-rendered, bot-tolerant, same Yahoo
   inventory PLUS PayPay Flea Market listings (marketplace PAYPAY_JP,
   listing_type fixed). Parse `li.itemCard` / `.itemCard__itemName` / first
   `.g-priceDetails__item .g-price` ("50,000 YEN", current not buyout) /
   href for auction|paypay ID. URLs are already Buyee links. Verified live.

6. **Topsun vs Topsun VS distinction.** Topsun originals (1995/97) vs Topsun
   VS battle series (1998-99, "X VS Y" titles) are different series at very
   different prices — VS listings were inheriting the original Charizard's
   ~$2,393 fair value. `comps._topsun_series()` in variant_conflict; only
   fires when "topsun"/"top sun"/トップサン present so plain "vs" elsewhere
   never triggers.

7. **Close-tracker (closer.py) — the anti-circularity fix.** Andrew flagged
   that expected-close derived from our own fair value is circular. Real
   fix: after every live run, look up recently-ended observed auctions BY
   ITEM ID (`ebay.com/itm/<id>` shows "Winning bid: US $x" for months),
   record real closes into the `closed` table (actual = winning bid +
   observed shipping; unsold = 0 to stop rechecks; undetermined = retry next
   run; bot-challenge detection; `scraping.close_lookups_per_run` = 20).
   Exact ID match, no fuzzy titles, no dependence on comps. This converts
   the ~900 banked observations into matched closes → unblocks the learner.
   Runs every scan (so 30-min sweeps keep `closed` current).

8. **Bid-aware close model + price-band settle ratios** (see §6). The
   parametric knobs (0.02/bid, 0.75 floor) are PLACEHOLDERS by design — the
   close-tracker feeds the learner, the learner measures predicted-vs-actual
   and only deploys the ML tier when it beats parametric out-of-sample. The
   system grades its own homework.

9. **Grail hunt (grails.py + Grails report tab + grail alerts).** Andrew's
   42 grails in config `grails:` in significance order (grail_score 100 top
   → 40 bottom; dict form `{query, weight, max_price}` overrides). Strict
   AND token matching with bidirectional synonym sets (auto==signed==
   autograph, 1st==first, gb==gameboy, pack==booster) + phrase normalization
   ("game boy"→gameboy). Best (highest-score) match wins on overlap. Every
   listing from every query is tagged (not just grail queries). A match only
   counts at **`grail_min_price` ($3000)+** — a $40 "Batman" listing is not
   the 1940 Batman #1. Per-grail `max_price` also supported. Grail rows are
   EXEMPT from the negative-EV output rules and from max_rows trimming (a
   grail at fair price is still a grail). Report "Grails" tab (purple),
   grouped by grail sorted by significance, cheapest 5 per grail. Grail
   alerts (alerts.py) fire on fresh $3k+ grail BINs (≤24h old) or grail
   auctions ending ≤24h — bypassing profit gates, capped 5/run, sorted by
   significance, leading the Telegram message.

10. **Velocity scoring** — Sales/mo + Ann ROI columns (see §6).

11. **Inventory & P&L (portfolio.py + Portfolio tab).** Andrew logs buys/
    sells in `portfolio.csv` (template auto-created; columns: date_bought,
    description, query, cost_basis, date_sold, sale_proceeds, notes). Full
    scans mark open positions to market using the run's fair values +
    `fair_history` fallback (matched on the `query` column), net of the same
    sell-side costs as the scanner (vault 7% ≥$500 else 13.25%). Report
    "Portfolio" tab: per-position value / P&L / CAGR + totals (open cost,
    market value, unrealized, realized). Rows without a query stay unmarked.

12. **Telegram digest (digest.py).** After every FULL scan (the daily 6pm
    cron — NOT the 30-min BIN sweeps): two messages — top 25 profit
    opportunities (by opportunity_score, positive EV, watches excluded) and
    top 25 **live-auction grails** (auction listing_type only, by
    grail_score, ties break soonest-ending, ended excluded). HTML,
    hyperlinked, chunked <4096 chars. Config `alerts.digest`
    {enabled, top_opportunities:25, top_grails:25}. This IS the "daily
    Telegram delivery" Andrew asked for — it rides the existing daily cron,
    no separate scheduler (that would double-send).

13. **Output rules (report only shows actionable rows).** GLOBAL: no row
    with expected_value < `output.min_expected_value` (0) or roi < 0 appears,
    any listing type. Pure auctions (no BIN option) additionally need ≥1 bid
    — a 0-bid "current price" is just the opening ask. Exempt: hybrid
    auction+BIN (`has_buy_now`, set from Browse API buyingOptions containing
    both AUCTION and FIXED_PRICE) and yahoo_jp (Buyee has no bid counts).
    Grails exempt entirely. CRITICAL past-bug fixed: an old "show raw rows
    rather than nothing" fallback could resurrect ALL unfiltered rows when
    filters emptied the list — removed; an empty run is now reported
    honestly ("no actionable deals this run").

14. **5-category report tabs** (Andrew's spec): **Pokemon Cards / Sports
    Cards / Video Games / Watches / Other**. `report._category()` +
    `CATEGORIES`. **Watches are quarantined** OUT of the Action tab AND the
    digest AND alerts (their valuation data — modifiers, box/papers,
    franken-watches — is less reliable); they live only in their own tab.

15. **scan.log (rotating, 2MB×3, next to config).** Every run logs start,
    comps counts, per-run alert gate summary ("N of M rows passed gates"),
    grail candidates, telegram delivered=True/False, digest messages,
    closer results, learner status. "Did alerts fire today and why/why not?"
    is now answerable from the file. Added because Andrew got zero alerts
    and there was no evidence to diagnose from.

16. **Alert delivery PROVEN.** Sandbox can't reach Telegram, but sent a real
    test via Chrome `javascript_tool` fetch to api.telegram.org — Andrew
    confirmed receipt (message delivered to chat 8589843164). The alert
    pipeline works end-to-end; the reason he'd seen zero alerts is that
    nothing had cleared the gates (soft valuations from empty comps + stale
    BINs correctly muted by freshness + Mac asleep through sweep windows).

17. **(2026-07-18) Bot-block root cause found + fixed: TLS fingerprinting.**
    scan.log from Andrew's real runs proved 130point 403s on EVERY request
    (never one success ever) and eBay HTML 418 "I'm a teapot"/403 on both
    sold-search and /itm/ closer lookups — while the same URLs work in
    Chrome. Plain python-requests is fingerprinted at the TLS/HTTP2 layer;
    headers can't fix that. Fix: `BaseScraper` now uses
    `curl_cffi.requests.Session(impersonate="chrome")` (real-Chrome TLS
    fingerprint) when curl_cffi is installed, falling back to plain requests
    with a logged warning when not. curl_cffi added to requirements.txt
    (Run Scan.command auto-installs; Test Comps.command now also pip-installs
    requirements first so it can verify immediately). curl_cffi does NOT
    install in the sandbox (like scikit-learn) — fallback path is what runs
    there; impersonation path unit-tested via a stubbed curl_cffi module
    (Session(impersonate) wiring, _HTTP_ERRORS catching, breaker trips).
    NOT yet confirmed live from his Mac — Test Comps.command is the proof.

18. **(2026-07-18) Ops hardening: run lock, caffeinate, ptcg breaker.**
    (a) Single-run lockfile (`.scan.lock`, fcntl) in main.py: overlapping
    scans (seen live 10:04 that morning — manual full scan + cron sweep
    interleaved) defeat politeness delays and contend on SQLite. BIN sweeps
    skip when locked (rc=0); full scans wait up to 15 min; --demo/--calibrate
    bypass. (b) `caffeinate -im` wraps the python call in both Run
    Scan/BIN Sweep .command scripts; new **Fix Mac Sleep.command** runs
    `sudo pmset -c sleep 0` (Andrew types his own password) so cron fires
    while plugged in. (c) pokemontcg.io: timeout 30s→8s + per-run circuit
    breaker (3 consecutive failures → skip rest of run) — it was eating
    repeated 30s timeouts every scan with no key configured. (d) Test
    Comps.command now tees output to `comps_test_result.txt` so the
    assistant can read the live-verification result from the sandbox.
    All unit-tested + --demo regression passed; keys verified intact.
    ALSO: his folder briefly vanished mid-session — an OS upgrade toggled
    iCloud Desktop sync and moved the whole folder into "Desktop - Andrew's
    MacBook Air (2)". He restored it. If paths ever 404 again, check that
    first (and keep this folder OUT of iCloud sync — it evicts history.db).

19. **(2026-07-25) Output-garbage audit + fixes (from Andrew's ask: "look
    matter-of-factly at what garbage is in the output").** Audited the
    07:52 live full scan: 17 of the top 25 Action rows were one set-wide
    query ("Pokemon No Rarity Set") sharing one mongrel fair value
    ($1,749) across totally different cards AND grades (PSA 1 Mewtwo, PSA
    3 Gyarados, raw Nidoking - all "worth" $1,749, agreement=2%). NOTE:
    that report predated the same-morning ungraded-fix (08:33) - always
    check mtimes before declaring a fix broken. Fixes shipped:
    (a) SUBJECT INJECTION (engine._valuation_query trigger 2): when the
    query has no subject tokens (set-wide/theme), the listing's own
    subject (top-3 longest alphabetic non-generic tokens) is injected into
    the valuation query, so a Mewtwo is valued on Mewtwo sales, not the
    set median. Rows get "valued on 'mewtwo' (set-wide query)" note +
    regraded=True (kept out of fair_history).
    (b) VALUE DISPUTED flag (Valuation.disputed): comps-vs-guide agreement
    <25% (>4x apart) means one source priced the wrong thing - confidence
    hard-capped at 0.30, note added, excluded from fair_history, excluded
    from Crossover tab.
    (c) Grade floor in _effective: CGC 1 counts as PSA 1, not "PSA 0".
    (d) Verified in the same audit: run-lock works in production (07:52:50
    sweep skipped itself), learner's ML close model is DEPLOYED ("ML close
    model" notes), comps flowing (84/39 comps rows), watches correctly
    quarantined out of Action.

20. **(2026-07-25) Crossover arbitrage flagger (Andrew's pick #5).** New
    "Crossover" report tab (teal): CGC/BGS/SGC/BVG listings whose PSA-
    equivalent edge_now (already net of fees/tax, grade shift applied)
    exceeds the PSA grading fee + min profit. Columns incl. Grade / As PSA
    / Grading Fee / Regrade Profit (= edge_now - fee), sorted by profit.
    Fee tiers by fair value: <=$500:$25, <=$1500:$75, <=$2500:$150,
    else $300 (config algorithm.crossover.fee_tiers). Config:
    algorithm.crossover {enabled true, min_profit 100}. Disputed-value and
    watch rows excluded. Assumes the -1 shift holds on regrade (CGC 10 ->
    PSA 9); comeback risk noted in About tab. write_report now takes
    config (main.py passes it).

21. **(2026-07-25) Pokemon grade floor.** Andrew: "filter out all pokemon
    cards psa 3 and below... just pokemon." output_ok drops graded rows
    with EFFECTIVE grade <= 3 (shift applies: CGC 4 == PSA 3 drops) when
    `report._category(query)` == "Pokemon Cards". Sports/games/watches/
    other untouched; grails exempt; ungraded unaffected (assumed PSA 5).
    Config `filters.pokemon_grade_floor` (default 3.0). Note there is NO
    25-row cap anywhere in the report (recent runs: 121/63/6/118 rows) -
    the only 25s are the Telegram digest sizes (alerts.digest config).

22. **(2026-07-25) Daily-review overhaul (Andrew approved all 5 recs).**
    His goal restated: "a short concise list of auctions/BINs I can review
    each day at the end of the day and decide where to place bids."
    (a) **"Today" tab, FIRST tab + active on open** (amber header):
    auctions ending <=24h in DEADLINE order (end_time asc), then fresh
    BINs (<=24h old) by score. Decide column (BID?/BUY?/OFFER?), and a
    bold **Max Bid** column = breakeven all-in price (report._max_bid:
    vault route resale*0.93-ship if >=vault_min, else taxed route
    resale*(1-fee)/(1+tax)-ship). Quality floors: EV >= $75, conf >= 0.25,
    disputed excluded (config output.today {hours, fresh_hours,
    min_expected_value, min_confidence}).
    (b) **Duplicate collapse** (report._collapse): same query + same
    effective grade + subject-token overlap + title_match >= 0.6 + no
    variant conflict => one row (best EV, tie cheapest), "+N more
    listing(s) from $X" note (Opportunity.dupe_note attr, rendered in
    Notes). Auctions/BINs collapse separately. Applied to Action + Today;
    category tabs keep the full book. Subject-overlap guard is REQUIRED -
    without it Gyarados merged into Mewtwo on shared context tokens
    (caught by test).
    (c) **Absolute end times** in _timing: "ends 9:42 PM today/tomorrow"
    (<=24h), "ends Wed 9:42 PM" (<=6d) - countdowns go stale by read time.
    (d) **Notes split**: Valuation.audit_notes (blend/comps only/guide
    only/ML close model/sniper floor) -> hidden "Model Detail" col Z;
    human notes stay in Notes (vault note shortened to "vault route (0%
    tax in / 7% out)", tax note to "+8% tax in"; "ends in Xh" note
    removed as redundant with Timing).
    All unit-tested (incl. Today-first tab order, deadline sort, floors,
    max-bid vault/taxed routes, collapse merge/no-merge); keys intact.
    Andrew reruns a fresh scan after this session - REVIEW THE NEW
    OUTPUT'S Today TAB WITH HIM TONIGHT.

---

## 8. CURRENT STATE — what works, what's open (grounded in real data)

WORKS (verified live as of 2026-07-25):
- **eBay comps FLOW IN PRODUCTION.** The 2026-07-18 blocker is resolved:
  curl_cffi chrome impersonation + homepage warm-up + persistent cookie
  jars + challenge cooldown-retry. Evidence: 07-25 reports show rows with
  84/39 matched comps; scan.log has repeated "challenge cleared after
  cooldown retry" lines (4x on 07-25 alone) and real comp-backed blends.
- **Learner reached Tier 2 sometime during the week** — 07:52 report rows
  carry "ML close model" notes, meaning model.pkl was trained AND beat
  parametric out-of-sample. (But see OPEN: joblib import broke later that
  day.)
- eBay listings: API (Browse) + HTML fallback, US+UK+DE. ~12k
  listings/full-scan. Buyee(Yahoo/PayPay) scraper works.
- PriceCharting guide (Andrew pays $50/mo); pokemontcg optional with 8s
  timeout + per-run breaker.
- Valuation model incl. 2026-07-18/25 overhaul: ungraded=PSA 5, per-listing
  regrade, set-wide subject injection, disputed flag, grade floor,
  pokemon_grade_floor, vault/tax cost model, velocity, bid-aware close.
- Report: Today tab (decision list w/ Max Bid) + Action (deduped) + 5
  category tabs + Grails + Crossover + Portfolio + Discovery + Movers +
  About; absolute end times; human/machine notes split.
- Ops: cron (6pm full + 30-min sweeps), single-run lockfile (verified live
  07-25: overlapping sweep skipped itself), caffeinate wrappers, scan.log,
  Telegram alerts + daily digest (delivery proven), close-tracker.

OPEN / UNVERIFIED / BROKEN (priority order):
- **[NEW 2026-07-25 ~13:20] learner: "could not load model.pkl (No module
  named 'joblib')"** in Andrew's afternoon reruns — the morning run loaded
  the ML model fine, so something changed in .venv between 08:45 and
  13:20 (possibly a pip sync under the moved/restored folder, or a partial
  scikit-learn/joblib install on py3.9). Harmless degradation (falls back
  to parametric settle ratios) but the ML tier is earned and should be
  restored: check `.venv/bin/pip show joblib scikit-learn`, reinstall if
  missing. FIRST THING NEXT SESSION.
- **130point — Cloudflare-walled.** 403 on EVERYTHING including its
  homepage, every impersonation profile (live matrix 07-18). Beyond TLS
  fixes. Options: cf_clearance cookie export from his real Chrome
  (fragile), route via Chrome extension (not cron-compatible), or accept
  eBay+PriceCharting as the comp stack (current de facto state; 130point's
  unique value = true sold DATES for velocity). Stays enabled in config —
  trips its breaker in ~10s/run, auto-recovers if unblocked.
- **Fanatics Collect — BLOCKED, Chrome session planned WITH ANDREW.** Their
  Algolia search key (app_id 3xt9c4x62i) was rotated; site is fully
  client-rendered (Next RSC), data path app.fanaticscollect.com/graphql.
  Needs live DevTools/Chrome-extension capture of the GraphQL listings
  query + auth, OR a fresh Algolia key. Andrew has agreed to do this
  together ("we will do fanatics and alt tomorrow" — said 07-18; still
  pending).
- **Alt.xyz — not started.** Same reverse-engineering class as Fanatics.
- **Mac sleep.** Andrew explicitly said he'll manage sleep himself (declined
  Fix Mac Sleep.command). If overnight sweeps are missing from scan.log,
  ask — don't re-push the pmset fix.
- **Learner calibration depth unknown** — sandbox can no longer read
  history.db (FUSE, see §3), so closed-count/settle-band status must come
  from `--calibrate` output (ask Andrew to run it) or scan.log learner
  lines.

---

## 9. config.yaml — complete knob map

Top-level keys: `watchlist`, `grails`, `grail_min_price` (3000), `sites`,
`api_keys`, `algorithm`, `scraping`, `output`, `filters`, `marketplaces`,
`fx_rates`, `bin`, `database`, `japan`, `alerts`, `misspell`.

- **watchlist:** list of `{query, priority?, discovery?, query_ja?,
  max_buy_price?}`. A grade in the query auto-flags priority. Priority =
  alerts enabled + sorted first + gets international + Japan coverage. He has
  ~101 entries (Pokemon 1st-ed/Topsun/Jungle/Fossil, sports RCs, watches,
  sealed games, plus discovery theme queries). He edits this himself.
- **grails:** 42 entries in significance order (see §7.9). Also mirrored as
  watchlist queries (he added them; names harmonized so the grail-query
  auto-add dedupes to zero extra searches).
- **sites:** [ebay, yahoo_jp, fanatics_collect]. (fanatics currently returns
  nothing — key rotated; harmless, fails to empty list.)
- **api_keys:** ebay {client_id, client_secret} WORKING; pricecharting
  {token} WORKING; pokemontcg {api_key} empty (optional); fanatics {app_id
  3xt9c4x62i, search_key} EMPTY + key rotated (blocked).
- **algorithm:** auction_settle_ratio 0.92, resale_fee_rate 0.1325,
  sales_tax_rate 0.08, tax_free_marketplaces [YAHOO_JP,PAYPAY_JP],
  psa_vault {enabled true, min_price 500, sell_fee_rate 0.07},
  min_title_match 0.55, outlier_mad_k 3.0, comp_half_life_days 30,
  late_auction_hours 6, cost_model_tau_hours 24, proxy_bid_per_bid 0.02,
  proxy_bid_cap 0.15, sniper_floor 0.75. (grader_grade_shift/grader_premiums
  available, default −1 point all non-PSA.) NEW (code defaults, not yet in
  config file): **ungraded_grade 5.0** (raw = assumed PSA 5),
  **crossover {enabled true, min_profit 100, fee_tiers [[500,25],[1500,75],
  [2500,150],[1e9,300]]}** (Crossover tab).
- **scraping:** request_delay_seconds 3.5, parallel_queries 6,
  site_delays {130point 1.5}, max_results_per_query 40,
  close_lookups_per_run 20, international_priority_only true,
  comps_warm_per_sweep 2, use_html_comps true, use_130point true.
  NEW (code defaults): **impersonate "chrome"** (curl_cffi browser profile
  — the one that beats eBay), **challenge_cooldown_seconds 20**.
- **output:** file opportunities.xlsx, min_expected_value 0, max_rows 1000.
  NEW (code defaults): **today {hours 24, fresh_hours 24,
  min_expected_value 75, min_confidence 0.25}** (Today tab floors).
- **filters:** min_value 1000, max_price 100000, exclude_keywords [...],
  flag_seller_feedback_below 10, too_good_ratio 0.35, min_listing_match 0.6,
  max_roi 2.0. NEW (code default): **pokemon_grade_floor 3.0** (graded
  Pokemon at effective <= 3 never shown; Pokemon only).
- **bin:** priority_only false (sweeps cover ALL queries so sports appear),
  freshness_half_life_hours 24, offer_target_ratio 0.8.
- **alerts:** enabled true, min_edge_now 150, min_roi 0.15, min_capture 0.5,
  min_confidence 0.15, priority_only true, macos_notification true, sound
  true, telegram {bot_token, chat_id 8589843164} WORKING, max_roi 2.0,
  grails {enabled, fresh_hours 24, ending_hours 24, max_per_run 5},
  digest {enabled true, top_opportunities 25, top_grails 25}.
- **japan:** proxy_fee_usd 10.0. **database:** file history.db,
  comp_cache_hours 48. **fx_rates:** USD/GBP/EUR/CAD/AUD/JPY/CHF.

---

## 10. Alert gating logic (alerts.py) — important nuance

Profit alerts gate on tradeable facts (edge $, ROI, capturability) with
confidence as a FLOOR, not multiplied (soft valuation sources cap confidence
low and would mute everything). Gates: edge_now ≥ min_edge_now, min_roi ≤
roi ≤ max_roi, confidence ≥ min_confidence, priority (unless
priority_only=false), not discovery, not SUSPICIOUS. **Capture gates BINs
ONLY** — a fresh underpriced BIN is real, a stale one is stale for a reason;
auctions with a big edge alert even days out (you want to watch/snipe them).
This type-aware gating was a KEY historical fix — previously capture≥0.5
gated everything, muting every big-edge auction (which is days out, low
capture) → Andrew got zero alerts. Dedup: an item is recorded "alerted" only
after a channel actually delivers (table `alerts`), so failed sends retry.
Grail alerts are a separate branch bypassing all profit gates (§7.9).

---

## 11. history.db schema (db.py)

Tables: `comps`(query,title,price,sold_date,url,site,scanned_at UNIQUE),
`fair_history`(query,ts,fair,n_comps), `observations`(item_id,site,query,
title,listing_type,price,shipping,bids,end_time,fair,predicted_settle,
hours_left,observed_at), `closed`(item_id PK,actual_price,closed_at),
`alerts`(item_key PK,alerted_at), `guide_cache`(query PK,value,ts). ITEM_ID
regex extracts eBay numeric id from /itm/ urls. `match_closed()` (title-fuzzy,
legacy) still runs when comps flow but closer.py is now the primary close
source; INSERT OR IGNORE dedupes the two. Last successful sandbox DB read
(2026-07-18): comps 0, fair_history ~1351, observations ~5743, closed 0,
alerts 1, guide_cache ~106. Since then comps clearly flow (84-comp report
rows on 07-25) and the ML tier trained (>=150 matched closes implied), but
the sandbox can no longer open the DB even read-only (§3) — use
--calibrate / scan.log for current counts.

---

## 12. Learner (learner.py)

Tier 0: config defaults. Tier 1 (≥20 matched closes): learned settle_ratio =
median(actual/fair) → learned_params.json, plus price-band ratios
(settle_bands lt500/500to2000/gte2000, each needs ≥20). Tier 2 (≥150 +
scikit-learn): gradient-boosted close model, deployed to model.pkl ONLY if
its CV MAE beats parametric by ≥3% out-of-sample. Refits after each full
scan. `ClosePredictor` is what the engine consumes at runtime
(`settle_ratio`, `settle_ratio_for(fair)`, `predict_ratio(...)`).

---

## 13. Backlog / recommended next actions (priority order)

1. **Fix the joblib/model.pkl load failure** (see §8 - NEW). Check
   `.venv/bin/pip show joblib scikit-learn`; a `pip install -r
   requirements.txt` from Run Scan.command may already heal it. Confirm
   "ML close model" notes return.
2. **Review the fresh scan's Today tab WITH Andrew** — he reran after the
   07-25 daily-review overhaul; first real look at Today/Max Bid/dedupe/
   Crossover output. Expect tuning requests (floors, max-bid margin,
   collapse aggressiveness).
3. **Fanatics reverse-engineering WITH Andrew at Chrome** (agreed, pending
   since 07-18). Capture the live GraphQL listings query + auth or a fresh
   Algolia key. He's "doing great on eBay but not much outside it."
4. **Alt.xyz** — same class, fourth venue.
5. **130point revival** (Cloudflare-walled; options in §8) or formally
   drop it and lean on eBay+PriceCharting (current de facto state).
6. **Mercari Japan** — waiting on Andrew's API access.
7. **Velocity-weighted Score** — fold annualized_roi into opportunity_score
   once he trusts the Sales/mo numbers (he explicitly deferred this).
8. **Channel-aware net proceeds** — eBay vs Probstein vs COMC etc., beyond
   the current eBay/vault model.
9. **Snipe sheet + calendar reminders** — offered 07-25 as part of top-5
   builds; he picked Crossover instead ("i dont love the options for
   now") — don't re-pitch unless auctions in Today start slipping.
10. **Raw-upside view w/ vision-model photo triage** — discussed and
    designed 07-25 (Claude API vision on surviving raw candidates, photo
    grade column, ~cents/card, needs HIS Anthropic API key in config);
    he was interested in automating the "eyeballing" but didn't pick it.
    Likely to come back once raw listings start surfacing under the
    assume-PSA-5 rule.

---

## 14. The .command helper scripts (what each does)

- **Run Scan.command** — full scan → dated xlsx in Opp Runs/. Pip-syncs
  requirements.txt every run (this is how curl_cffi arrived), runs under
  `caffeinate -im`.
- **Run BIN Sweep.command** — fast BIN sweep (all queries) → BIN runs/.
  Caffeinated; does NOT pip-sync (venv changes need a full scan first).
- **Setup Telegram.command** — one-time chat-id finder.
- **Test Comps.command** — pip-syncs deps, runs ONE query through the real
  eBay + 130point scraper code, prints results AND tees to
  `comps_test_result.txt` so the assistant can read the outcome from the
  sandbox after Andrew double-clicks. (Was temporarily a 5-profile
  impersonation diagnostic matrix on 07-18; simplified back once eBay's
  winning combo was found.)
- **Test Alerts.command** — sends a labeled TEST telegram via the real
  `_send_telegram` code path + prints recent alert lines from scan.log.
- **Fix Mac Sleep.command** — one-click `sudo pmset -c sleep 0`. Andrew
  DECLINED to use it (manages sleep himself) — it exists, don't re-push.

Keep these in mind: they are Andrew's entire interface besides config.yaml.
When you build something he needs to trigger or verify, a `.command` file is
the right delivery mechanism (chmod +x it).

---

## 15. Report tabs, in order (report.py)

**Today** (FIRST + active on open; amber; the end-of-day decision list:
auctions ending <=24h in DEADLINE order then fresh BINs by score; Decide
column BID?/BUY?/OFFER?; bold **Max Bid** = breakeven all-in,
vault/tax-aware; floors EV>=$75 conf>=25%, disputed excluded),
**Action** (top-scored + ending/fresh-soon positive-EV, watches EXCLUDED,
duplicate cards COLLAPSED to best listing with "+N more from $X" note),
**Pokemon Cards**, **Sports Cards**, **Video Games**, **Watches**, **Other**
(category tabs, only created if non-empty, UNcollapsed full book),
**Grails** (purple; by significance; cheapest 5 per grail; not a profit
view), **Crossover** (teal; CGC/BGS/SGC cheap in PSA-equivalent terms;
Regrade Profit = edge_now - PSA fee tier), **Portfolio** (P&L
mark-to-market; only if portfolio.csv has rows), **Discovery** (broad theme
queries, soft valuations, browse-only, quarantined from alerts+ML),
**Movers** (30d fair-value trend), **About** (column definitions).
Main-sheet columns: Rank, Pri(★), Type, Site, Title (hyperlink), Query,
Price, Ship, Bids, Timing (ABSOLUTE for auctions: "ends 9:42 PM today" /
"ends Wed 9:42 PM", amber <6h; "listed 2h ago" green when fresh), Fair
Value, Trend 30d, Comps Val, Guide Val, #Comps, Exp Cost, Expected Value,
Edge Now, ROI, Sales/mo, Ann ROI, Capture, Conf, Score, Notes
(human/decision only), Model Detail (hidden col Z: blend/agreement/ML
diagnostics). Audit columns M-P grouped/hidden. Whole dollars >=$1k, ▲/▼
trend, colored types, banded rows, data bars on Edge Now, color scales on
Expected Value / Score / Ann ROI.

---

## 16. Session 2026-07-25 afternoon — lock fix, silent-crash root cause, PriceCharting backoff

**A. "Another scan is already running" on every manual run — FIXED (main.py).**
- It was NOT a stale lock (flock releases on process exit). It was real
  contention: cron BIN sweeps (:00/:30) now take 15–20+ min each (eBay
  bot-challenge cooldowns + PriceCharting 429s), so `.scan.lock` was held
  almost continuously and every manual run collided with a sweep.
- New behavior (Andrew chose "kill the old one and start the new one asap"):
  - `.scan.lock` now records `<pid> <mode> <started>` for the holder.
  - BIN sweeps still skip when the lock is held; the message now names the
    holder ("another scan is already running (pid X, mode=Y, started Z)").
  - Full/manual scans PREEMPT the holder: SIGTERM, SIGKILL after 20s, then
    take the lock. The old "wait up to 15 min" behavior is gone.
- Verified live: 13:00 cron sweep logged the informative skip against the
  12:41 manual run's lock.

**B. Silent crash that killed EVERY run 09:30–13:30 today — ROOT-CAUSED &
FIXED (main.py).**
- Symptom: no xlsx output all day. Every run (manual full scans 09:37,
  10:41, 12:41 AND all cron sweeps) died immediately after logging
  "dropped N too-good-to-be-true rows", before the "report:" line. No
  traceback in scan.log — it only went to the Terminal window's stderr.
- Root cause: `from report import _category` INSIDE main()'s digest block.
  A function-local import makes `_category` local to ALL of main(), so when
  the 09:10 update (item 21, Pokemon grade floor) made `output_ok`
  reference `_category`, every run raised
  `NameError: free variable '_category' referenced before assignment in
  enclosing scope` on the first graded listing and died.
- Fix: removed the function-local re-import (module top-level import
  already exists). A comment at that spot warns not to re-add it.
- Hardening so this class of failure can't be silent again:
  - `output_ok` wrapped in try/except — logs the listing + full traceback,
    keeps the row (a bad row must never kill the report).
  - `__main__` now wraps main() and `log.exception`s any unhandled error
    into scan.log before exiting 1.
- Verified: the 13:30 cron sweep (running the instrumented code) logged the
  exact NameError at 13:48, then COMPLETED end-to-end: 89 actionable rows,
  2 Telegram alerts delivered, wrote `BIN runs/bin_sweep_2026-07-25_13.30.xlsx`
  — first successful output since 09:17.

**C. PriceCharting 429 storm — circuit breaker (valuation/price_guide.py).**
- Runs were sending hundreds of requests into a rate-limit wall. Now: after
  10 CONSECUTIVE request failures, PriceCharting is not called again for
  the rest of the run (mirrors the existing `_ptcg_fails` pattern). Counter
  resets on any success. Logs "pricecharting: 10 consecutive failures -
  backing off for the rest of this run" once.
- Cache-poisoning guard: misses caused by request errors (429/timeout/
  breaker open) are NEVER persisted to guide_cache. Previously a 429 storm
  cached NULL for every query hit during the storm for the whole 7-day TTL.
- The 429s look like genuine rate limiting on Andrew's plan; if they
  persist on fresh runs consider adding a small per-request delay or
  checking PriceCharting's documented limits.

**D. Notes / caveats for the next session.**
- scan.log entries today with `demo=True` (13:20, 13:23, ~13:52) plus a
  "joblib" warning came from sandboxed test runs during this session —
  ignore them.
- `bin_sweep_2026-07-25_13.30.xlsx` may contain a few rows the Pokemon
  grade floor should have filtered ("keeping row" fallback fired during
  that run, pre-fix). All later runs filter correctly.
- Andrew should rerun Run Scan.command for a full report (Today tab review
  from item 22 is still pending). With A+B fixed a manual run now starts
  instantly (preempting any sweep) and completes with output.
- Files changed this session: `code/main.py` (lock preemption + holder
  info, _category import fix, output_ok try/except, __main__ crash
  logging), `code/valuation/price_guide.py` (429 breaker + no caching of
  errored misses).

---

## 17. Session 2026-07-25 evening — live acceptance, Goldin/Heritage, exit optimizer

This section supersedes older notes that describe Goldin/Heritage as
inactive, Fanatics as only a rotated-key problem, Buyee as healthy, the
default exit as eBay, or `Model Detail` as column Z.

### Live acceptance result

The production acceptance run passed all 105 pre-change tests, then exited
1 because it could not obtain live inventory. That outcome was correct:

- eBay Browse: three HTTP 429s, persistent API breaker opened.
- eBay sold HTML: repeated bot challenges, persistent HTML breaker opened.
- Buyee/Yahoo JP: HTTP succeeded across priority queries but the expected
  `li.itemCard` markup was absent.
- Stale comp caches were available, but no live listings existed to score.

Do not interpret that run as "there were no bargains." It means production
ingestion was unavailable, exactly as the Source Health controls reported.

### Marketplace recovery

- `scrapers/goldin.py` uses the current public `lots_v2` POST service and
  validates `searchalgolia.lots`. Live smoke test returned three real
  Michael Jordan PSA 9 lots. Goldin's 22% buyer premium and $19 minimum
  now flow through landed cost and bid inversion.
- `scrapers/heritage.py` uses `sports.ha.com` and parses live `/itm/`
  anchors by their `Current Bid` block rather than brittle CSS class names.
  curl_cffi live smoke returned three real listings. Heritage's 22% buyer
  premium and $29 minimum are modeled.
- `goldin` and `heritage` were added to local `config.yaml` sites.
- Parser canaries write `site/parse` outcomes for Goldin, Heritage,
  Fanatics and Yahoo/Buyee; Source Health now distinguishes transport
  success from a broken response schema.
- Fanatics remains disabled. Its old Algolia adapter is retained with a
  canary, but the current app's production public-search hostname had no
  DNS and anonymous GraphQL was rejected. Recheck upstream before more
  reverse engineering.

### Automatic exit optimizer

- `economics.best_exit_route` compares eligible configured venues by net
  proceeds. It supports percentage/tiered fees, fixed costs, value bounds,
  category allowlists and a `requires_graded` rule.
- `Listing.resale_channel` defaults to `auto`; explicit watchlist and
  portfolio overrides are honored.
- Local auto candidates: eBay (13.25%) and Goldin Marketplace (8.3%,
  eligible graded cards/games, $100 minimum). Heritage and Fanatics are
  manual-only until dependable seller terms/access are known.
- PSA Vault is exclusive whenever its 0%-tax acquisition route applies.
  This prevents mixing vault tax treatment with an ordinary marketplace
  exit.
- Engine EV/Edge, Today Max Bid/Breakeven and Portfolio marks all use the
  same route.
- Excel main sheets: Best Exit, Exit Fee, Net Proceeds and vs eBay were
  inserted after Score. Notes is AC and hidden Model Detail is AD.
- Today: Best Exit, Net Proceeds and vs eBay sit before Max Bid.
- Portfolio: Best Exit is visible beside the mark.

Validation: 114 unit/regression tests pass; demo report writes successfully;
live Goldin and Heritage smoke tests returned real inventory.

### Next three

1. Repair/replace Yahoo-Buyee listing ingestion, whose markup or delivery
   behavior changed.
2. After eBay breaker cooldown, rerun full acceptance and visually review
   the first real Today sheet with automatic exits.
3. Add verified seller fee/eligibility models for Probstein and COMC; add
   Fanatics only after its public inventory service is reachable again.
