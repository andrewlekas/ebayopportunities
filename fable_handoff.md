# Card Arbitrage Scanner — FULL Context Handoff (Fable → Opus)

You are picking up an in-progress project for **Andrew**. Read this ENTIRE
document before touching anything. It supersedes HANDOFF.md (same folder) —
that file describes the state as of the morning of 2026-07-25; THIS file
adds the full afternoon session (crash fix verification, circuit-breaker
overhaul, speed overhaul) and corrects anything that changed. Where the two
disagree, this file wins.

Last updated: 2026-07-25 evening. Live state in §9 is grounded in the
15:44 full-scan log; later changes are summarized in §15.9.

---

## 1. Who Andrew is and what he wants

Former ETF trader deploying hundreds of thousands of dollars into buying
and reselling collectibles — graded Pokemon primarily, plus sports cards,
sealed vintage video games, watches, comics. Also hunts personal-collection
"grails" regardless of profit.

The system: scrapes auctions + BINs across marketplaces, values items
against real market data (self-improving), outputs a ranked Excel report
(profit + separate grails view), sends Telegram alerts + daily digest, runs
on cron, learns from observed auction closes.

He is NOT a developer. He interacts via double-clicking `.command` files
and editing `config.yaml` — never make him read code. Explain in trader
terms (edge, ROI, capital velocity, mark-to-market). Be CONCISE — he
explicitly values brevity and dislikes verbosity. He is sharp, catches
real bugs by eyeballing output/logs, and when he pastes rows or log lines
saying something's wrong, he is usually right — reproduce it as a unit
test, fix the root cause, prove it with the test.

---

## 2. Where everything lives

Everything is in `~/Desktop/ebay opportunities/` on his Mac:

**LAYOUT CHANGED 2026-07-25 EVENING** — Andrew asked that the top level
hold only what HE touches. Everything else moved into named folders. Code
resolves every path against config.yaml's folder via `code/paths.py`, so
nothing depends on the working directory any more.

```
ebay opportunities/
  Run Scan.command          # double-click: full scan -> "reports/Opp Runs/" (pip-syncs deps, caffeinated)
  Run BIN Sweep.command     # double-click: fast BIN sweep -> "reports/BIN runs/" (caffeinated, no pip-sync)
  Run Tests.command         # NEW: 44 regression tests + config check + --demo
  Setup Telegram.command    # one-time telegram chat-id finder
  Test Comps.command        # live test of comps scrapers
  Test Alerts.command       # sends a TEST telegram via the real code path
  Fix Mac Sleep.command     # sudo pmset fix - Andrew DECLINED it, don't re-push
  config.yaml               # ALL settings + API keys (the only file he edits)
  HANDOFF.md                # morning handoff (superseded by this file)
  fable_handoff.md          # THIS FILE
  NEXT_RUN_CHECKLIST.md     # what to verify after the fixes (written for Andrew)

  database/history.db       # SQLite: comps, guide cache, observations, closed, alerts
  model/learned_params.json # learner output (settle ratios, ML status)
  model/model.pkl           # learner ML model (present but NOT deployed - see 15.3)
  portfolio/portfolio.csv   # his positions ledger (he edits in Excel)
  logs/scan.log(.1/.2)      # rotating run log (2MB x 3) - evidence of every scan
  test results/             # test_results.log, comps_test_result.txt
  reports/Opp Runs/         # dated full-scan xlsx outputs
  reports/BIN runs/         # dated BIN-sweep xlsx outputs
  state/.breaker_state.json # persistent cross-run circuit-breaker state
  state/.cookies_*.json     # persistent per-site cookie jars (auto-managed)
  state/.scan.lock          # single-run lockfile: "<pid> <mode> <started>"
  setup/requirements.txt    # incl. curl_cffi, scikit-learn, joblib
  docs/                     # README.md, FEATURES.md
  .venv/                    # must stay at top level - every .command calls it
  code/
    paths.py                # NEW: single source of truth for every location
    main.py                 # orchestrator + CLI + 3-phase pipeline + lock + output rules
    models.py               # Listing, SoldComp, Valuation, Opportunity dataclasses
    report.py               # Excel generation (all tabs, formatting)
    alerts.py               # Telegram + macOS alerts + SHARED telegram breaker
    digest.py               # post-scan top-25 profit + top-25 grail digest
    grails.py               # personal-collection grail matching/scoring
    closer.py               # auction close-tracker (real final prices by item id)
    portfolio.py            # inventory & P&L mark-to-market
    db.py                   # SQLite helpers + schema
    learner.py              # self-improving settle-ratio / ML close model
    misspell.py             # typo-variant query generator
    demo_data.py            # synthetic data for --demo
    scrapers/
      base.py               # HTTP, politeness, breakers (per-run + persistent), cookies
      ebay.py               # eBay Browse API + HTML fallback (the workhorse)
      yahoo_jp.py           # Japan via Buyee (works)
      point130.py           # 130point comps - DISABLED in config (Cloudflare)
      fanatics_collect.py   # DISABLED in config (rotated Algolia key)
      goldin.py, heritage.py# dead placeholders
    valuation/
      comps.py              # comp matching, conflict filters, velocity, subject guard
      price_guide.py        # PriceCharting + pokemontcg.io (+ breakers, Retry-After)
      engine.py             # valuation model (fair value, EV, capture, score, guide-skip)
```

**SANDBOX QUIRKS (critical for the assistant):**
- SQLite WRITES to history.db from the sandbox throw "disk I/O error"
  (FUSE); as of 07-25 even read-only opens can fail. DB migrations go in
  code that runs natively on his Mac. Ground truth = scan.log + report
  xlsx (openpyxl reads work fine).
- The sandbox proxy blocks external hosts (eBay, Telegram, Buyee → 403).
  You CANNOT hit live sites from bash. Use Claude-in-Chrome browser tools
  to inspect live sites; `--demo` + unit tests to verify code. Telegram
  sends work from Chrome's javascript_tool (fetch api.telegram.org).
- His Mac `.venv` binaries don't run in the sandbox (macOS builds) —
  `.venv/bin/pip` failing there is NORMAL, not breakage.
- Sandbox test pattern: `rm -rf /tmp/oppo && mkdir -p /tmp/oppo && cp -r
  "<folder>/code" "<folder>/config.yaml" "<folder>/requirements.txt"
  /tmp/oppo/ && cd /tmp/oppo && python3 -m py_compile ... && python3
  code/main.py --demo -o /tmp/t.xlsx` plus unittest.mock unit tests.
  curl_cffi + scikit-learn do NOT install in the sandbox (fallback paths
  run there; impersonation paths are unit-tested via stubbed modules).
- ALWAYS edit the real copy under the Desktop folder; test on the /tmp
  copy. Bash sees the folder at a different mount path than Read/Edit.
- His folder once vanished mid-session: an OS upgrade moved it into
  iCloud "Desktop - Andrew's MacBook Air (2)". If paths 404, check that.

---

## 3. How to run / test

- **Primary regression:** `python3 code/main.py --demo -o /tmp/t.xlsx`
  (synthetic data, no network/keys). Run after EVERY change.
- CLI: `--mode all|auctions|bin`, `--demo`, `--calibrate`, `-o`, `-c`, `-v`.
- Cron on his Mac: full scan daily 6pm, BIN sweep every :00/:30. Both
  caffeinated. Lock: sweeps SKIP when locked (logs holder pid/mode/start);
  full/manual scans PREEMPT the holder (SIGTERM→SIGKILL after 20s).
  --demo/--calibrate bypass the lock.
- After ANY config edit, verify keys still present:
  `python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); assert
  c['api_keys']['ebay']['client_id'] and c['alerts']['telegram']['bot_token']"`
  He hand-edits config between sessions (trimmed watchlist 101→90 entries
  today) — re-read it, don't assume.
- Never handle his API keys directly; he pastes them himself.
- Keep a task list (TaskCreate/TaskUpdate). Lead with outcomes. When he
  reports a bad output row, reproduce from his exact paste as a unit test.

---

## 4. THE 2026-07-25 AFTERNOON SESSION (this session) — every change

Chronological, with WHY. All tested (unit + --demo), keys verified after
each batch. This is the section the morning HANDOFF.md lacks.

### 4.1 Verified the silent-crash fix (no new code)
Morning context (HANDOFF.md §16B): every run 09:30–13:30 died silently
after "dropped N too-good-to-be-true rows" — a function-local
`from report import _category` in main()'s digest block made `_category`
local to ALL of main(), so `output_ok` (which the same-morning Pokemon
grade-floor feature made reference `_category`) raised NameError on the
first graded listing. The previous session FIXED it (module top-level
import at main.py:27, output_ok wrapped in try/except keep-row, __main__
logs unhandled crashes to scan.log) but crashed before verifying.
This session PROVED it: bytecode inspection (no `_category`/`grade_info`
in main()'s locals/cells), the exact crashed listing ("1986 Fleer Michael
Jordan #57... PSA 3" — sports, so grade floor correctly keeps it), and a
full simulated live run (run_live monkeypatched, demo=False, mode=all)
exercising lock→filters→portfolio→write_report→alerts→digest→closer→
learner: rc=0, xlsx written. CONFIRMED IN PRODUCTION: 14:01 full scan
wrote 140 rows; 15:44 scan wrote 137.

### 4.2 Circuit breakers on every endpoint (batch 1)
Andrew's rule: ANY endpoint that fails N straight gets left alone for the
rest of the run (N was 10, lowered to 3 later — see 4.6). Audit found
three gaps, all closed:
- **eBay OAuth** (`ebay.py _get_token`): was re-POSTing a dead auth
  endpoint once per query (~200/run). Now `_oauth_fails` counter, stops
  at 3, resets on success.
- **Fanatics** (`fanatics_collect.py _algolia`): counted failures into
  `_streaks["api"]` but NEVER CHECKED the count — posted once per query
  forever. Now checks `lane_tripped("api")` first (announce-once).
- **Telegram** (alerts.py + digest.py): no breaker at all. Now a SHARED
  module-level breaker in alerts.py — `TG_TRIP_AFTER = 3`,
  `_tg_breaker = {"fails": n, "announced": bool}`, helpers
  `telegram_blocked()` / `telegram_result(ok)`; digest.py imports them.
  Consecutive failed sends across alerts AND digest count together; reset
  on any success. Failed alerts still retry next run (the `alerts` table
  only records actual deliveries).
Already covered before this session: scrapers' per-lane breaker
(base.py `_streaks`, `trip_after` = scraping.circuit_breaker_failures,
default 3), PriceCharting (was 10), pokemontcg.io (3).

### 4.3 Speed overhaul (three fixes, all shipped)
Evidence: 13:30 sweep spent ~18 of 20 min in fetch; PriceCharting was
called per-listing (hundreds of calls) because SUBJECT-INJECTED valuation
queries are unique per listing and always miss the guide cache → also
caused the 429 storms.
- **Guide-skip** (`engine.fair_value`): when a listing has >= 8 matched
  comps (`algorithm.guide_skip_min_comps`, default 8 — comps weight
  saturates there anyway), skip the PriceCharting lookup entirely.
  audit_note "guide skipped (N comps)". TRADEOFF: no comps-vs-guide
  disputed check on those rows (fine — 8+ subject-guarded comps is the
  stronger signal). Thin/no comps → guide consulted exactly as before.
- **Sweep pre-warming** (config): `comps_warm_per_sweep` 2 → 6. 48
  sweeps/day × 6 keeps all ~90 queries inside the 48h comp cache window,
  so the 6pm full scan skips most serialized eBay sold-page fetches.
- **Parallelism + fail-fast** (config + base.py): `parallel_queries`
  6 → 10 (API lane is quota-limited not politeness-limited; the html
  lane stays strictly serialized by the per-scraper lock, delays
  unchanged). New `scraping.html_timeout_seconds` (12): html-lane GETs
  give up at 12s instead of 30s; API lane keeps 30s.

### 4.4 Anti-spam overhaul (batch 2 — Andrew: "back off big time")
His log paste showed endless `bot-challenge page detected (1/3)` — the
challenge cooldown-retry usually CLEARED the challenge, which reset the
failure streak, so the breaker never tripped and every query burned a 20s
cooldown while eBay kept flagging us. Fixes:
- **Run-wide challenge tally** (`base.note_challenge(lane)`): every
  challenge counts (even ones that clear on retry). At
  `scraping.challenge_backoff_after` (now 3) challenges in one run: hard
  backoff — lane tripped for the rest of the run, cookie jar wiped,
  cooldown PERSISTED (below). closer.py challenges also feed this tally
  (its own scraper instance, so tallies are per-instance; the persisted
  state unifies them across instances/runs).
- **Persistent cross-run breaker** (`.breaker_state.json`): any lane trip
  (failure streak OR challenge tally) writes
  `{"<site>/<lane>": {strikes, last_trip, until}}`. New runs load it and
  refuse to contact that site/lane until `until` passes ("cooling off ...
  not contacting site until HH:MM"). Exponential backoff:
  `breaker_cooldown_minutes` (30) × 2^(strikes-1), capped
  `breaker_cooldown_max_hours` (24). Strikes escalate if the new trip is
  within 24h of the last (so flapping still escalates: 30m→1h→2h→…);
  a quiet 24h resets to strike 1. After expiry the next run probes
  normally — self-heals the moment a site unblocks. Writes are guarded by
  a class-level threading.Lock. VERIFIED LIVE: 130point escalated strike
  1 (30m, 14:32) → strike 2 (1h, 15:37); eBay html tripped at 10
  challenges 15:37 and the 15:44 run correctly refused eBay html
  ("cooling off ... until 16:07") while the Browse API lane kept working.
- **429s never auto-retried**: the plain-requests fallback client had
  urllib3 Retry with 429 in status_forcelist — answering "too many
  requests" with two more requests. Removed (5xx retries kept). The
  curl_cffi path (what actually runs on his Mac) has no internal retries.
- **Retry-After honored** (price_guide): on a PriceCharting 429 with a
  Retry-After header, pause guide lookups exactly that long (bounded
  30s–3600s, monotonic clock, `_pc_wait_until`); pauses are counted as
  fetch-errors so they never poison guide_cache.
- **Phase timing** in scan.log: `timing: fetch Xs, valuation+db Xs
  (N queries, W workers)` per run + `timing: closer Xs`. "Why was this
  run slow" is now one grep away.

### 4.5 Race + retry-spam fixes (found from the 15:37 log)
- **Breaker race**: log showed `request failed (4/3)…(10/3)` AFTER a
  backoff was announced — parallel workers had passed the entry check,
  queued on the html lock, then fired anyway. Fix: `do_request()`
  re-checks `lane_tripped` at the last moment (inside the lock for html).
  Test: 12 queued calls → only 3 hit the wire.
- **Cooldown-retry spam**: challenge handling now bails BEFORE the 20s
  sleep and again after it if the lane tripped meanwhile (another
  thread's challenge). 10 challenge-hit queries: ~4s, was 50s+.

### 4.6 Thresholds 10 → 3 everywhere (Andrew's call)
challenge_backoff_after 3 (also written into config.yaml explicitly),
OAuth 3, PriceCharting 3, Telegram 3. Scraper failure streaks and
pokemontcg were already 3. Everything network-touching now stops within
3 consecutive failures.

### 4.7 Disabled dead sources (Andrew's call, both trivially reversible)
- **130point**: `scraping.use_130point: false` (+ explanatory comment).
  Cloudflare 403s EVERYTHING incl. its homepage since ~07-18, every
  impersonation profile. Its unique value was true sold DATES for
  Sales/mo velocity (eBay comps are fetch-dated, coarser). Revival paths:
  cf_clearance cookie export from his real Chrome, or accept
  eBay+PriceCharting as the permanent comp stack.
- **fanatics_collect**: commented out of `sites:` list. Rotated Algolia
  key; had logged 626 "no search_key - skipping" lines while returning
  zero listings. Revival needs a Chrome session WITH Andrew to capture
  fresh credentials (GraphQL at app.fanaticscollect.com or new Algolia
  key) — he agreed to this back on 07-18, still pending, he wants more
  non-eBay volume.

### 4.8 Results (15:44 full scan, first run on all-new code)
**2m39s end-to-end** (was 29 min at 14:01 under challenge fire, ~20 min
sweeps): fetch 87s, valuation+db 67s, closer 0s. 137 actionable rows
written, 1 fresh alert delivered (Gengar PSA 9 $1,133 vs $2,500 fair),
digest 3 messages, learner n=383 ML DEPLOYED (cv mae 0.287 vs parametric
1.126). The morning "joblib missing" problem self-healed via Run
Scan.command's pip-sync (joblib was already in requirements.txt).
Andrew: "the run went great... big improvements."

---

## 5. Data model (models.py)

`Listing`: site, title, url, current_price, shipping, bid_count, end_time,
image_url, listing_id, query, priority, discovery, misspell_from,
listing_type ("auction"|"fixed"), best_offer, has_buy_now (auction that
ALSO has BIN), grail (matched grail name), grail_score (40–100),
created_at, currency, marketplace, seller_feedback. Properties:
age_hours, total_cost_now (price+shipping), hours_remaining.

`SoldComp`: title, price (incl shipping if known), sold_date (datetime!),
url, site.

`Valuation`: fair_value, comps_value, guide_value, n_comps, dispersion,
confidence, expected_cost, expected_value, edge_now, capture, trend_30d,
roi, sales_per_month, annualized_roi, opportunity_score, regraded (fair
value recomputed at LISTING's grade; kept out of fair_history), disputed
(comps vs guide >4x apart; conf capped 0.30; excluded from fair_history/
Crossover/Today), notes (human), audit_notes (model diagnostics → hidden
"Model Detail" col).

`Opportunity`: listing + valuation (+ report may attach dupe_note).

---

## 6. Valuation model (engine.py + comps.py) — current rules

- **Fair value**: blend of comps + guide. Comps weight grows with n
  (saturates ~8), shrinks with dispersion, floored 0.35. NEW: guide is
  SKIPPED when n_comps >= algorithm.guide_skip_min_comps (8) — see §4.3.
  Fallbacks: comps-only → guide-only (×0.95) → ask-based (25th pct of
  live asks ×0.9, conf ≤0.35, flagged) → 0.
- **Comp hygiene**: fuzzy title match ≥0.55, MAD trim k=3, 30d-half-life
  recency weight. Excluded: grade_conflict, language_conflict,
  variant_conflict (holo/1st-ed/shadowless/Topsun-vs-TopsunVS),
  subject_missing (query subject tokens must appear in title).
- **Grade normalization**: non-PSA graders (CGC/BGS/SGC/BVG) = one point
  LOWER than PSA. UNGRADED = assumed PSA 5 (comps.UNGRADED_GRADE,
  algorithm.ungraded_grade). Per-listing regrade: listing at a different
  grade than its query is re-valued at the LISTING's grade (regraded=True,
  excluded from fair_history). Set-wide queries: listing's own subject
  injected into valuation query. Effective grades floor at 1. Guide grades
  <7 map to loose (raw) price.
- **Close model** (auctions): interpolate bid-trajectory vs settle anchor
  (resale × settle_ratio 0.92 default / learned / price-band / ML).
  Proxy premium +0.02/bid cap 0.15; sniper floor 0.75×settle inside 6h.
  BINs: capture decays with listing age (half-life 24h).
- **Costs** (Andrew's exact rules): 8% sales tax on eBay checkout;
  YAHOO_JP/PAYPAY_JP exempt. PSA vault: eBay items ≥$500 → no tax in,
  7% all-in exit fee REPLACING the 13.25% marketplace fee. Routing is
  scenario-aware (edge_now at current price, EV at expected close).
- **Score** = min(roi,1) × confidence × capture. sales_per_month from
  comp velocity; annualized_roi = roi×365/cycle (NOT in score yet — he
  deferred). Defenses: exclude_keywords, too_good_ratio 0.35 (capture
  ×0.2 + SUSPICIOUS), low feedback ×0.7, filters.max_roi 2.0 drops
  absurd-ROI rows.

## 7. Output rules (main.py output_ok — the crash site, now hardened)

Report only shows actionable rows: EV ≥ output.min_expected_value (0),
roi ≥ 0; pure 0-bid auctions dropped (hybrid has_buy_now + yahoo_jp
exempt); graded Pokemon at effective grade ≤ filters.pokemon_grade_floor
(3) dropped (Pokemon only, grails exempt, raw unaffected); grails always
kept. output_ok is try/except-wrapped: a bad row LOGS with traceback and
STAYS (never kills the report). No fallback ever resurrects filtered rows
— an empty run reports honestly. Watches quarantined to their own tab
(out of Action/digest/alerts).

## 8. Network layer (scrapers/base.py) — post-overhaul reference

Per-site scraper; curl_cffi `Session(impersonate="chrome")` when
installed (his Mac), plain requests fallback (sandbox). Per-site delay
(`request_delay_seconds` 3.5, `site_delays`), html lane serialized by
per-instance lock; api lane concurrent with tiny jitter. Homepage warm-up
+ persistent cookie jars (wiped on challenge). Timeouts: html 12s
(`html_timeout_seconds`), api 30s. Per-lane failure streaks trip at
`circuit_breaker_failures` (3); challenge tally trips at
`challenge_backoff_after` (3); both persist to `.breaker_state.json` with
exponential cooldown (30m × 2^(strikes-1), cap 24h, strikes reset after a
quiet 24h); `do_request` re-checks the breaker post-lock (race fix);
urllib3 fallback never auto-retries 429. eBay challenge pages (HTTP 200)
are detected by markers; first 2 challenges get a 20s-cooldown retry,
3rd trips. Telegram breaker lives in alerts.py (shared with digest).
PriceCharting: 3-fail per-run breaker + Retry-After pause + never caches
error-misses. pokemontcg.io: 8s timeout, 3-fail per-run breaker.

## 9. CURRENT STATE (grounded, end of 2026-07-25)

WORKS:
- Full pipeline: 15:44 scan = 2m39s, 137 rows, alert delivered, digest
  sent, ML close model deployed (n=383). Crash bug dead (verified 3 ways).
- eBay Browse API (US+UK+DE), Buyee/Yahoo+PayPay, PriceCharting (with
  breakers), grails, portfolio, closer, learner, lock preempt/skip,
  Telegram alerts+digest, Today/Action/category/Grails/Crossover/
  Portfolio/Discovery/Movers tabs, timing lines, persistent backoff.
- Watchlist: 90 entries (he trimmed from 101 today, parses clean).

DISABLED (deliberate, reversible one-liners in config):
- 130point (`use_130point: false`) — Cloudflare-walled.
- fanatics_collect (commented out of `sites:`) — rotated key.

WATCH ITEMS (not broken, keep an eye):
- eBay html challenge-walls intermittently; breaker now handles it
  (backoff, cookie wipe, self-heal probe). Comps lean on warm caches
  and stale-cache fallback during cooldowns; closer retries pile up
  ("pending retry") and catch up when the wall lifts.
- pokemontcg.io flaps 500s (breaker handles; it's optional, no key).
- Buyee returned "no item cards" on ~30 JP queries at 14:01 — could be
  thin listings or markup change; verify live in Chrome if JP stays empty.
- Overnight sweeps missing → Mac asleep (he manages sleep himself; don't
  re-push the pmset fix).
- The 3× repeated "cooling off" announce lines = one per scraper instance
  (main + closer create separate instances). Cosmetic only.

## 10. config.yaml — knob map (NEW keys marked)

Top-level: watchlist (90), grails (42), grail_min_price 3000, sites
[ebay, yahoo_jp], api_keys (ebay WORKING, pricecharting WORKING,
pokemontcg empty-optional, fanatics empty-dead), algorithm, scraping,
output, filters, marketplaces, fx_rates, bin, database, japan, alerts,
misspell.

- algorithm: auction_settle_ratio 0.92, resale_fee_rate 0.1325,
  sales_tax_rate 0.08, tax_free_marketplaces [YAHOO_JP,PAYPAY_JP],
  psa_vault {enabled, min_price 500, sell_fee_rate 0.07}, min_title_match
  0.55, outlier_mad_k 3, comp_half_life_days 30, late_auction_hours 6,
  cost_model_tau_hours 24, proxy_bid_per_bid 0.02, proxy_bid_cap 0.15,
  sniper_floor 0.75, ungraded_grade 5 (code default), crossover {enabled,
  min_profit 100, fee_tiers}, **guide_skip_min_comps 8 (NEW, code default)**.
- scraping: request_delay_seconds 3.5, **parallel_queries 10 (was 6)**,
  site_delays {130point 1.5}, max_results_per_query 40,
  close_lookups_per_run 20, international_priority_only true,
  **comps_warm_per_sweep 6 (was 2)**, use_html_comps true,
  **use_130point false (was true)**, impersonate "chrome",
  challenge_cooldown_seconds 20, **html_timeout_seconds 12 (NEW)**,
  **challenge_backoff_after 3 (NEW)**, circuit_breaker_failures 3
  (code default), **breaker_cooldown_minutes 30 / breaker_cooldown_max_hours
  24 (NEW, code defaults)**.
- output: file, min_expected_value 0, max_rows 1000, today {hours 24,
  fresh_hours 24, min_expected_value 75, min_confidence 0.25}.
- filters: min_value 1000, max_price 100000, exclude_keywords,
  flag_seller_feedback_below 10, too_good_ratio 0.35, min_listing_match
  0.6, max_roi 2.0, pokemon_grade_floor 3.
- alerts: enabled, min_edge_now 150, min_roi 0.15, min_capture 0.5 (BINs
  ONLY — key historical fix, auctions alert without capture gate),
  min_confidence 0.15, priority_only true, telegram {bot_token, chat_id
  8589843164} WORKING, max_roi 2.0, grails {...}, digest {enabled,
  top_opportunities 25, top_grails 25}.
- bin: priority_only false, freshness_half_life_hours 24,
  offer_target_ratio 0.8. japan: proxy_fee_usd 10. database: file,
  comp_cache_hours 48 (guide_cache_days 7).

## 11. history.db schema (db.py)

comps(query,title,price,sold_date,url,site,scanned_at UNIQUE),
fair_history(query,ts,fair,n_comps), observations(item_id,site,query,
title,listing_type,price,shipping,bids,end_time,fair,predicted_settle,
hours_left,observed_at), closed(item_id PK,actual_price,closed_at),
alerts(item_key PK,alerted_at), guide_cache(query PK,value,ts).
closer.py is the primary close source (exact item-id lookups of ended
/itm/ pages); learner Tier 1 ≥20 matched closes (settle ratio + price
bands), Tier 2 ≥150 + sklearn (GBM deployed only if CV MAE beats
parametric by ≥3%). CURRENT: n=383, ML DEPLOYED.

## 12. Report tabs (report.py, in order)

Today (FIRST+active; amber; auctions ending ≤24h by deadline then fresh
BINs by score; Decide + bold Max Bid = breakeven all-in, vault/tax-aware;
floors EV≥$75 conf≥25%; disputed excluded) · Action (deduped via
_collapse: same query+grade+subject → best EV row + "+N more from $X";
watches excluded) · Pokemon/Sports/Video Games/Watches/Other (full book)
· Grails (purple; by significance; cheapest 5/grail) · Crossover (teal;
CGC/BGS/SGC cheap in PSA-equivalent terms; Regrade Profit = edge_now −
fee tier) · Portfolio (P&L, only if portfolio.csv has rows) · Discovery ·
Movers · About. Absolute end times ("ends 9:42 PM today"); human notes in
Notes, model diagnostics in hidden col Z.

## 13. Backlog (priority order)

1. **Review the Today tab WITH Andrew** (pending since the daily-review
   overhaul; expect tuning of floors/max-bid margin/collapse).
2. **Fanatics revival — Chrome session WITH Andrew** (capture GraphQL/
   Algolia creds; he wants non-eBay volume; agreed 07-18, still pending).
3. **Alt.xyz** — same reverse-engineering class, not started.
4. **130point revival** (cf_clearance cookie export) or formally accept
   eBay+PriceCharting as the comp stack. Currently disabled.
5. **Buyee empty-results check** (live Chrome look if JP stays empty).
6. **Velocity-weighted score** (fold annualized_roi in — he deferred
   until he trusts Sales/mo).
7. **Channel-aware net proceeds** (eBay vs Probstein vs COMC).
8. Mercari Japan (waiting on his API access). Snipe sheet + raw-photo
   vision triage: designed, not picked — don't re-pitch unprompted.

## 14. Read-first checklist for the next session

1. `tail -100 scan.log` — check overnight runs: timing lines, breaker
   behavior (strikes escalating? self-healed?), alert deliveries.
2. `ls -lt "Opp Runs" "BIN runs" | head` — outputs flowing on schedule?
3. `cat .breaker_state.json` — who's in cooldown, what strike.
4. `python3 -c "...yaml..."` keys-intact check (he edits config himself).
5. If he reports a bad row: reproduce from his paste as a unit test first.
6. **Double-click `Run Tests.command`** (or read `test_results.log`) — 36
   regression tests + config check + `--demo`. Run it after ANY change.

---

## 15. SESSION 2026-07-25 EVENING (Opus) — valuation & learning fixes

Andrew asked for two config rules and approved four bug fixes I found by
auditing the live DB and the 15:44 report. All changes are tested; nothing
here required network access. **He has NOT yet done his manual verification
run** — that is the first thing to review next session.

### 15.1 What was broken (evidence, not theory)

- **The learner was training on garbage.** `learned_params.json` said
  `settle_ratio 1.2756` — auctions closing 28% ABOVE fair value. Root
  cause: `run_live` records an observation for every listing with
  `fair_value > 0`, but the `filters.min_value` ($1,000) floor is not
  applied until the report step. 5,907 of 19,232 observations carried a
  fair value under $10 (a graded Jungle Gloom valued at $2.85). The old
  `actual < fair*5` guard then discarded 65% of rows and took the median of
  a biased tail. `--calibrate` reported a median actual/fair of **25.2**.
  Effect: expected costs inflated, EV crushed, 201 rows dropped for
  negative EV on the 16:00 sweep, 0 alerts.
- **The ML model deployed on a rigged benchmark.** Gate was "beat
  parametric by 3%", and parametric MAE was 1.126. Cross-validation also
  leaked: 1,090 training rows came from only 207 auctions (5.3 snapshots
  each), so the same item sat in train and test folds.
- **The price guide ignored the cross-grader shift.** `_pricecharting`
  picked its field from the RAW grade, so a CGC 10 was priced off PSA 10.
  The `or graded-price` fallback also let low grades inherit Grade-9 money.
- **`GRADE_RE` accepted two-digit grades.** "CGC 85" (a seller typo for
  CGC 8.5) parsed as grade 85 → "PSA 84". Live in the Crossover tab
  claiming $3,349 regrade profit on a $1,886 listing.
- Combined result: `guide_cache` held Topsun Charizard at **$6,718 for
  PSA 9, CGC 9, CGC 8.5 and "CGC 85"** — four grades, one price.

### 15.2 What changed

Config (`config.yaml`):
- `filters.pokemon_grade_floor: 5` (was running on the code default of 3).
- `filters.exclude_keywords` += repack, poster, art print, chase box,
  set-break, set break. Removes 2 live rows: a resin art print ($897 EV)
  and a "Chase Box" repack ($979 EV).
- `algorithm.crossover` block written out explicitly (enabled, min_profit,
  PSA fee tiers) — the -1 grade penalty rule is now visible and tunable.
- Surfaced hidden defaults: `output.today`, `alerts.grails`,
  `algorithm.ungraded_grade / guide_skip_min_comps /
  capture_half_life_hours`, `scraping.circuit_breaker_failures /
  breaker_cooldown_minutes / breaker_cooldown_max_hours`.
- New learner knobs: `min_observation_fair` (50), `learner_min_fair` (50),
  `learner_min_comps` (3), `learner_ratio_band` ([0.1, 3.0]),
  `ml_max_cv_mae` (0.25).

Code:
- `comps.py`: `GRADE_TOKEN_RE` (broad, for stripping) vs validated
  `grade_info()`. Grades >10 are rejected → the title reads as UNGRADED
  (errs DOWN, never up). SGC's legacy 100-point labels translate
  (92→8.5, 96→9, …). `grade_info` now returns the NORMALISED grade, so
  report/crossover show "SGC 8.5 → PSA 7.5". `grader_of`, `grade_conflict`,
  `_tokens`, `robust_comp_value` all route through it. Rejected tokens are
  tallied in `comps.UNPARSEABLE_GRADES` and logged once per run.
- `price_guide.py`: prices at the PSA-EQUIVALENT grade via
  `_guide_cents()` — a ladder (loose anchored at `ungraded_grade`, then
  cib=7, new=8, graded=9, box-only=9.5, manual-only=10) read by linear
  interpolation. Never rounds up; refuses (returns None) rather than quote
  a higher rung. `GUIDE_CACHE_VERSION` purges the cache once when the rule
  changes — without it this fix would have been invisible for 7 days.
  Logs each distinct grade→field routing to scan.log.
- `db.py`: `observations` gains `n_comps` + `confidence` (additive
  `_migrate()`, runs on his Mac at next scan; `record_observation` writes
  by column name so it works pre- and post-migration).
- `main.py`: `output_ok` moved to MODULE LEVEL (it was a closure inside
  `main()` — precisely the shape that caused the 07-25 NameError outage;
  nothing can shadow it now) and takes a `drops` counter, so scan.log gets
  a per-reason breakdown. Observations/`fair_history` gated on
  `min_observation_fair`. New log lines: trust-floor skips, unparseable
  grades.
- `learner.py`: training rows filtered on fair floor + `n_comps` present
  and >= 3 + ratio band; **Tier 1 medians one row per AUCTION** (was per
  snapshot); Tier 2 needs 150 distinct auctions, GroupKFold by item, and
  must clear an absolute MAE bar; params are ALWAYS written (the early
  return used to leave stale settle ratios and a deployed model on disk).

### 15.3 Current learner state — deliberately cold

Every closed auction in the DB was observed 07-14..07-20, i.e. under
valuation code that has since been fixed (median actual/fair by day:
07-14 193x, 07-17 219x, 07-18 8x, 07-20 1.5x). None of it is fit to learn
from, so the filters correctly reject all 1,090 rows and
`learned_params.json` was reset to `n: 0`. **The engine is back on
Andrew's hand-tuned `auction_settle_ratio: 0.92`** — Tier 0, the system's
own cold-start design. The learner rebuilds automatically as observed
auctions close under the fixed code (closer settles up to 20/run); Tier 1
re-engages at 20 trustworthy closes, ML at 150. Do NOT "fix" this by
loosening the filters to get a number back.

`model.pkl` is still on disk but is NOT used (`ml.deployed: false`); the
learner logs that explicitly each run.

### 15.4 Verification done / still to do

Done: 36 unit tests (each pinned to a real row or number), `--demo`
regression, config keys re-verified, bytecode check that `main()` has no
shadowed globals, guide-cache purge tested against a copy of the real
`history.db`, learner round-trip proven (60 synthetic auctions at a true
settle of 0.85 → recovers 0.85 ±0.06, MAE 0.077 vs the old 1.126).

Still to do — **Andrew's manual run**: full scan, then inspect the report
and `scan.log`. What to check is in `NEXT_RUN_CHECKLIST.md`.

### 15.5 Folder reorganisation + run footer (same session, after 16:45)

- **Every run now ends with two lines**, on every exit path including
  crashes and skipped sweeps: an API tally (`ok / failed / skipped` per
  endpoint, where "skipped" means the breaker was open and we chose not to
  call) and `=== run finished in 2m 38s (mode=bin, exit=0) ===`.
- Andrew asked to "apply the API throttling to the BIN script". There is
  no separate BIN script — `Run Scan.command` and `Run BIN Sweep.command`
  both run `code/main.py`, the latter with `--mode bin`. Every breaker
  lives in the shared network layer, so sweeps were already covered. The
  API tally now makes that visible rather than asserted. One genuine
  BIN-only gap WAS found and fixed: the comp-warming loop (`mode == "bin"`)
  checked its source's breaker once at the start and then walked the whole
  watchlist regardless; it now stops the moment the source trips.
- Layout moved to the folder tree in §2. `code/paths.py` is the single
  source of truth; `load_config` resolves `database.file` against
  config.yaml's folder, which also kills the old CWD dependency.
- Also fixed: five leaked file handles in the cookie/breaker-state writes.
- Verified in production: the 16:30 cron BIN sweep ran on the new code
  (before the move) — migration applied, 10 observations written WITH
  n_comps, 879 junk valuations rejected by the trust floor, guide cache
  purged, `CGC 85 x6` / `CGC 605 x2` rejected by the grade parser.

### 15.6 Code review round 2 (same session) — five more fixes

Found by auditing the whole scanner against the live DB and the 15:44
report; all five approved by Andrew and shipped with tests.

1. **Subject injection picked seller adjectives, not cards.** On
   subject-less queries the engine injected "the three longest leftover
   title words". Measured over 599 real listings the top injected
   "subjects" were `symbol x116, wotc x67, tcg x51, mon x50, lp x20` —
   a Jungle Snorlax was valued against `investment beautiful centering`.
   THIS IS THE ROOT CAUSE of the junk fair values behind §15.1. Now
   `comps.subject_candidates()`: context/marketing words stripped (~90
   added to GENERIC_TOKENS), >=3 letters, earliest-in-title first, 2
   tokens (`algorithm.subject_tokens`). Same 599 listings now yield
   `machamp, kabuto, charmander, zapdos, raichu, aerodactyl…` — all real
   cards.
2. **Accented characters fragmented every token.** Tokenizers split on
   `[a-z]`, so "Pokémon" became `pok` + `mon`; 277 of 4,075 real titles
   (7%) are non-ASCII. New `code/textutil.fold()`, applied in comps
   tokenizers, grails and `_category`. **Careful:** it strips accents only
   from LATIN letters — Japanese dakuten decompose identically, and a
   naive NFKD fold turned トップサン into トッフサン (caught by a test).
3. **Hybrid auction+BIN listings threw away the takeable price.**
   `_parse_summary` kept `currentBidPrice` and discarded `price`. A
   zero-bid hybrid was therefore priced off the seller's opening ask —
   live example: a $499 opening bid on a card valued at $2,821, reported
   as +$1,584 EV. Now `Listing.buy_now_price` is captured; a zero-bid
   hybrid is priced at the BIN (that example flips to −$1,286 and leaves
   the report), expected close is capped at the BIN, and `output_ok` only
   exempts hybrids whose BIN price we actually know.
4. **Max Bid was exact breakeven.** Now two columns: **Max Bid** (leaves
   `output.today.max_bid_target_roi`, default 15%) and **Breakeven** (grey,
   zero profit). `_max_bid` became `_bid_levels`.
5. **`_category` sent sealed Pokemon to Video Games** — "sealed" was a
   game keyword tested before Pokemon, which ALSO let those rows skip the
   Pokemon grade floor. Split into `GAME_PLATFORM_KW` (nes/gameboy/xbox…,
   checked first) and `GAME_TITLE_KW` (mario/zelda…, checked after
   Pokemon); "sealed" removed from both.

Test count 44 -> 64. Still flagged, still not fixed: JP proxy fee is a flat
$10 with no international shipping; `Sales/mo` is matched-comp-count / 3
now that 130point is off.

### 15.7 Collecting standards + card-level valuation (end of session)

Andrew's asks: Pokemon = 1st Edition / No Rarity only; more video games
represented; and "the 1984 Jordan and 1933 Ruth results all share one fair
value though they are different cards - value down to the card number and
grade".

**What the data showed.** "Babe Ruth 1933" = 116 comps, $1 to $21,000,
median **$6** - the pool mixed 1933 Goudeys with "1991 Conlon ... You Pick"
lots. "Michael Jordan 1984 Star" mixed #101/#288/#195/#7/#26 ($550 to
$16,800) into one $29 median. Three causes: `exclude_keywords` was never
applied to COMPS (415 of 8,114 were banned words), no year discrimination
(a 2009 Bowman tribute priced the 1948 Mikan), no card-number
discrimination.

**Shipped.**
- `comps`: new `year_conflict`, `card_number`, `number_conflict` guards,
  wired into `robust_comp_value` and `comp_velocity`. number_conflict is
  deliberately ASYMMETRIC - once the query names a number, an UNnumbered
  comp is rejected too (admitting them held the median at $29).
- `main.run_live` screens comps with `exclude_keywords` and logs the count.
- `engine._valuation_query` gained a third trigger: inject the listing's
  card number. Skipped for `discovery` queries (browsing; pinning them
  would empty the Discovery tab).
- **Fallback ladder** (`engine.evaluate`): card+grade specific valuation
  needs `algorithm.min_specific_comps` (3). Below that it falls back to the
  query's mixed median, flags the row "MIXED POOL ... NOT a bid target",
  caps confidence at `mixed_pool_confidence_cap` (0.25), and is BARRED from
  alerts (`alerts.passes`) and the Telegram digest.
- `main.collection_ok` (new, module level): Pokemon must show 1st Edition
  or No Rarity unless the query names an exempt vintage set (topsun /
  carddass / movie promo); video games must be sealed or graded. Grails
  exempt from both. All config-driven under `filters`.
- WATA + VGA added as graders (VGA's 100-point scale / 10; no PSA shift -
  game graders are never cross-referenced to PSA). `_completeness()` makes
  sealed / CIB / loose a variant conflict.
- `filters.min_value_by_category` - Video Games floor $250 vs $1,000.
- `demo_data` rewritten to 1st Ed Pokemon + a numbered vintage sports card
  + a sealed graded game, so `--demo` still exercises the real standards.

**KNOWN AND DELIBERATE - read before "fixing" it.** With card number AND
grade pinned, the existing pools yield 0-2 comps, so the broad sports
queries now fall back to MIXED POOL, and their medians are under the
$1,000 floor - meaning those rows go QUIET rather than showing a wrong
number. That is intended. The remedy is a query per card, NOT loosening
the guards.

**OPEN - awaiting Andrew's approval** (drafted from cards with real comp
support in his own data; he wants to sanity-check the Jordan numbers since
1984-85 Star has parallel sets):
Jordan 1984 Star #288 / #7 / #26 · Ruth 1933 Goudey #149 / #53 ·
Mikan 1948 Bowman #69 · Ted Williams 1939 Play Ball #92 ·
Messi 2004 Megacracks #71 / #35 / #62 · Ty Cobb T206 #150 / #460.
Add these, optionally grade-specific, and retire the broad queries they
replace. Then per-number comp FETCHING (his "later" option) once eBay's
blocking settles.

Test count 64 -> 81.

### 15.8 Self-test gate (last change of the session)

Every live scan now runs the full regression suite BEFORE doing anything
and aborts if any check fails. Deliberate design points:

- Lives in `main.py` (`run_self_test` + the gate just after `load_config`),
  NOT in the .command files, so it protects cron runs too.
- Runs as a SUBPROCESS - the tests patch logging and build throwaway
  databases, none of which should touch a real scan's state.
- Fails CLOSED: a suite that cannot run (import error, timeout) counts as
  a failure. Exit code **2** = tests failed, distinct from 1 = no deals.
- Aborts BEFORE the lock, so a failing build never takes the lock, hits
  eBay, writes to the database or produces a report. Verified by injecting
  a real regression (removing CGC's -1 shift): 3 checks failed, exit 2, no
  report, no lock, database byte-identical.
- `--demo` and `--calibrate` are exempt - they are how you diagnose.
- Escape hatches: `--skip-self-test` once, or
  `self_test.before_every_scan: false` in config.
- Both .command files report exit 2 as "TEST RUN FAILED" rather than the
  generic error message.

Cost: ~2s per run. The log line on success is
`self-test: all 81 checks passed`.

Not touched (flagged, not fixed): `fair_history` still contains old wrong
fair values (affects the Movers tab and portfolio marks; self-corrects as
new rows land). `report._max_bid` defaults `psa_vault.enabled` to True
while `engine` defaults it to False — no live effect since config sets it
explicitly. `learner`/`ClosePredictor` read `learned_params.json` relative
to the CWD; the `.command` files `cd` first, so this only matters if a
scan is ever launched from elsewhere.

### 15.9 Original improvement plan completed: items 7–10

All four remaining improvements shipped together and are covered by the
105-test regression suite.

7. **Crossover is now a strict card allowlist.** `report._crossover_tab`
   accepts only `algorithm.crossover.allowed_graders` (default CGC/BGS/SGC/
   BVG) and `allowed_categories` (Pokemon/Sports). WATA and CGC-graded video
   games cannot enter this sheet.
8. **One complete landed-cost equation.** `Listing` now carries explicit
   buyer/proxy fees, domestic and international shipping, insurance, import
   duty and FX spread. Yahoo Japan populates them from `japan` config. The
   valuation engine and Excel Max Bid/Breakeven invert the same equation.
   Exit fees are configurable by `resale_channel` (default eBay; Goldin,
   Heritage and Fanatics defaults included).
9. **Historical state quarantined.** `fair_history.trusted` is additive;
   6,261 legacy rows migrated to `NULL` and were preserved in
   `database/history.db-pre-fair-trust-20260726T013536Z`. Only new,
   centrally approved rows can drive trends or portfolio marks. Guide cache
   version was bumped and stale guide values cleared.
10. **Persistent source health.** Every live run writes a readiness snapshot
    to SQLite: success/failure/breaker counts, disabled sources and comp-cache
    freshness. Reports include a color-coded `Source Health` tab, and the log
    names sources needing attention.

The Japan duty assumption defaults to 15% and is deliberately configurable.
It is a conservative planning input, not customs advice; verify the final
proxy quote and exact HTS classification before a large order.
