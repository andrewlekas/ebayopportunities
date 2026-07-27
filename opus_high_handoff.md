# Card Arbitrage Scanner — Opus High Handoff

**Written 2026-07-26 ~14:00 America/Chicago.** Reconciled directly against the
working tree, Git history, SQLite databases, breaker state, `logs/scan.log`,
the latest full-scan and BIN workbooks, and a full run of the regression suite.

This file **supersedes** `solextrahighhandoff.md` / `sol extra high handoff.md`,
`HANDOFF.md`, `fable_handoff.md`, `docs/README.md`, `docs/FEATURES.md`, and
`NEXT_RUN_CHECKLIST.md`. Those remain useful history — the reasoning behind the
safeguards is worth reading — but where they disagree with this file, this file
is correct. Section 2 lists exactly what changed and why.

---

## 0. State in one page

A local Mac collectibles arbitrage scanner for Andrew. It searches live auctions
and fixed-price listings, values them from sold comps and price guides, models a
realistic acquisition price, calculates net resale proceeds, picks the best exit
channel, and writes one Excel workbook per run. It also tracks positions, sends
Telegram alerts/digests, learns from auction closes, and persists source health.

```text
root:    /Users/alekas/Desktop/ebay opportunities
branch:  main
HEAD:    4034523  Harden scan reliability and expose filter diagnostics
remote:  origin/main = 0ce1780   (local is 3 commits AHEAD, 0 behind)
tests:   227 passing
```

**Fair value was re-architected on 2026-07-26 (section 1A).** It is now keyed
on a structured card identity and led by PriceCharting product resolution,
replacing a design where the value was a function of the watchlist query
string. Read section 1A before touching valuation.

The working tree is **not** clean, and that is intentional. A large, tested,
interdependent implementation set is uncommitted. Preserve it. Never use
`git reset`, `git checkout --`, or selective restoration.

Live source reality right now:

- **eBay Browse API — healthy.** 423 OK / 0 failed in the last full scan.
- **eBay sold-comp HTML — cooling.** 5 strikes, escalated to an 8-hour
  cooldown expiring ~14:58 CDT. The comp cache is frozen at 6,951 rows and is
  now ~71h old against a 48h freshness limit.
- **Goldin — healthy.** 184 OK, real inventory, full landed-cost model.
- **Pristine — degraded but working.** 176 OK / 4 transient 403s, 338 raw rows.
- **Heritage — cooling.** Breaker expired ~13:49 CDT; should be usable again.
- **Yahoo/Buyee — parser failure.** HTTP 200 on all 20 queries, 0 parsed.
- **Fanatics Collect, ALT — implemented but disabled.** No authorized endpoint
  or local export configured. This is an access dependency, not missing code.
- **130point — disabled.** Cloudflare-walled.
- **PokemonTCG.io — disabled**, and as of today it genuinely makes no calls.
- **Learner — deliberately cold.** `n: 0`, `ml.deployed: false`. Correct:
  historical training evidence was contaminated by earlier valuation bugs.

**The engineering bottleneck is no longer sourcing. It is valuation identity.**
The scanner collects thousands of live listings and produces a complete,
auditable workbook. But broad query-level comp pools are still assigned to
materially different objects, parallels, and product classes. Do not respond to
this by lowering the report floors — that increases junk without improving
value precision.

---

## 1. What changed in this session (2026-07-26 ~14:00)

Four defects were found by direct inspection and fixed. Test count went
**142 → 156**. Each fix has regression tests that were verified to **fail
against the pre-fix code** — they are real regressions, not tautologies.

### 1.1 Stray guide database in the project root — FIXED

`PriceGuide.__init__` connected with `dbc.get("file", "history.db")`, a bare
relative default that bypassed `code/paths.py`. Any config that had not been
through `main.load_config`'s path resolution silently created a **second**
guide cache in whatever directory the process started in. A 28KB
`history.db` (guide_cache / guide_meta / guide_product_cache) was sitting in
the project root, competing with the real 20MB `database/history.db`.

- `code/valuation/price_guide.py` gained `guide_db_path(config)`, which
  resolves relative paths against `config["_config_dir"]` and defaults to
  `paths.DEFAULT_DB` (`database/history.db`), never a bare filename.
- The stray file contained 0 guide_cache rows and 0 guide_product_cache rows —
  no data was lost. It was moved (not deleted) to
  `database/history.db-stray-root-20260726-recovered`.
- Tests: `TestGuideDatabaseLocation` (4 tests), including one that builds a
  guide from an unrelated cwd and asserts no stray DB appears beside the
  process.

### 1.2 PokemonTCG.io said "disabled" while making live calls — FIXED

`source_health._configured_sources()` marks the source disabled when
`api_keys.pokemontcg.api_key` is empty (it is). But `PriceGuide._pokemontcg`
sent **anonymous** requests anyway. The 11:54 full run reported
`pokemontcg/guide  disabled  7 ok  10 failed  348 skipped` — a source cannot be
both disabled and live, and an operator has to be able to trust Source Health.

- `_pokemontcg` now returns immediately (recording `skipped`) when no API key
  is configured. Runtime behavior now matches the advertised status.
- Side benefit: removes a source that was failing 10 of 17 calls with HTTP 500s.
- Tests: `TestPokemonTcgKeyGate` (3 tests), one of which explicitly **binds
  `source_health.capture()` to actual guide behavior** so the two can never
  drift apart again.

### 1.3 BIN sweeps overran their own schedule — FIXED

The eBay 500-result ceiling went live between the 12:30 and 13:00 BIN sweeps.
Observed effect:

```text
10:00 sweep   3m 15s   50 kept   max eBay result 120
10:30 sweep   3m 28s   48 kept
11:00 sweep   3m 37s   48 kept
12:30 sweep   3m 37s   50 kept   max eBay result 120
13:00 sweep   58m+ and still running when this was written
              one query returned 1,277 eBay rows; 14 queries returned the full 500
13:30 sweep   SKIPPED - the 13:00 sweep still held the lock
```

Left alone, roughly every second sweep would be dropped.

- `code/main.py` gained `site_result_caps(scfg, mode)`. A new
  `scraping.max_results_per_query_by_site_bin` map overlays the base per-site
  map for **BIN mode only**. Full scans keep their depth.
- `config.yaml`: BIN eBay cap set to **100**; full-scan eBay cap stays **500**;
  every other connector stays at the 40 default.
- Tests: `TestBinResultCaps` (5 tests), including a guard asserting the shipped
  `config.yaml` can never let a sweep dig deeper than a full scan.

**Still outstanding — requires Andrew:** the BIN sweep cadence should move from
every 30 minutes to hourly. That schedule lives in the Mac's `crontab`/launchd,
which this environment cannot read or write. See section 9.1 for the command.

### 1.4 Pristine shipping was $0 — FIXED

`marketplace_costs.pristine.shipping_estimate` was `0`, understating delivered
cost on every Pristine row. No Pristine row had reached Action yet, so nothing
was mispriced in production — but the moment one did, Max Bid would have been
too high by the entire shipping amount.

- Set to **$15/lot** (Andrew's call). This deliberately overstates a combined
  invoice: erring high biases toward passing on a marginal lot rather than
  overpaying for one.
- Tests: `TestPristineLandedCost` (2 tests). A $100 hammer now lands at $132
  ($100 + 17% premium + $15 shipping), was $117.

### 1.5 Deliberately NOT done

- **Guide pre-screen for below-floor rows.** 91% of valued rows (3,813 of
  4,209) are discarded at the fair-value floor, many after paying for a
  PriceCharting call. Skipping the guide for those is the single biggest
  runtime win available, but it touches the valuation path and could change
  which rows survive. Andrew chose to defer it to its own change with its own
  regression tests. See section 9.2.
- **Deleting the duplicate handoff files.** `solextrahighhandoff.md` is an
  untracked hardlink duplicate of the tracked `sol extra high handoff.md` —
  two names, one inode. Left in place; removing user files is Andrew's call.

---

## 1A. The fair-value re-architecture (2026-07-26, later session)

### What was wrong

`fair_value(query, comps, asks)` never received the listing. Both of its
inputs were keyed on the **watchlist query string**:

* comps came from a table keyed `UNIQUE(query, url, price)`;
* the guide came from `_pricecharting_product_key(query)`, which strips the
  grade and hands PriceCharting a **search phrase**. One phrase returns one
  product, so `disney chrome 2023` returned a single product standing in for
  a 750-card set.

Measured on the 11:54 workbook: **4,237 valued rows carried 5 distinct
(comps, guide) opinions.** Worse, the dependency was inverted —
`guide_skip_min_comps: 8` meant the guide was consulted *only* when comps
were thin, and the blend weight floor of 0.35 then handed it 50–65% of the
vote. In the two rows that reached Action, PriceCharting set **65%** of the
value. Guide-inflated values also clear the $1,000 floor more easily, so they
were ~5x over-represented in Action versus research rows.

The disagreements were not PriceCharting being wrong. They were broad
queries: `Bandai Carddass Pokemon PSA` returned comps $92 vs guide $15,873,
and both are plausibly correct — *for different cards*.

### What it is now

**`code/valuation/identity.py`** — a frozen `CardIdentity` extracted from the
title: object class, year, subject, card number, parallel/finish, serial
denominator, auto/relic, grader, numeric grade, **grade qualifier**
(Authentic/Altered), and variant. It exposes `fingerprint()` (the grouping
key), `specificity()` (0–1, gates bid eligibility), `conflicts_with()` (why
two things are different assets, in words), `guide_query()`, and
`score_candidate()`. Extraction is memoised via `identity_of()` because a
full scan parses ~500k titles.

Verified on the real failures: **Disney 8 → 8 distinct assets, Superman
6 → 6, Mikan 5 → 4** (the two that merged are both raw #69 — correctly one
asset).

**PriceCharting resolution** now uses `/api/products` (plural, up to 20
candidates), scores every candidate against the identity, and either lands a
product or refuses and says why. `/api/product?id=N` then supplies prices.
Product IDs are stable, so resolution is cached permanently.
`PriceGuide.quote(identity) -> GuideQuote` carries `value`, `match`
(exact/strong/weak/none), `score`, `product_id`, `genre`, `sales_volume`,
`epid` and the grade-routing explanation.

**Order of authority** (Andrew's rule):

1. exact/strong product match → **the guide sets the value**; identity-matched
   comps may pull it **down** but never inflate it, and only with at least
   `min_specific_comps` sales;
2. otherwise identity-filtered comps;
3. otherwise the unfiltered median, labelled `IDENTITY UNRESOLVED` and
   `MIXED POOL`, browse-only.

`IDENTITY UNRESOLVED` is a `quality.NOTE_BLOCKERS` entry, so an unresolved row
can never reach Today, Action, alerts, the learner or portfolio marks.

**Other changes:**

* Comps are now matched against the **card**, not the query. This mattered:
  a `Disney Chrome 2023` search has no grade, so it read as PSA-5-equivalent
  and rejected every PSA 10 sale *of the very card being valued*.
* Grader-specific top-grade fields are used — `condition-17-price` (CGC 10),
  `condition-18-price` (SGC 10), `bgs-10-price` — falling back to the
  one-grade-down shift when absent. Those fields already price the CGC/SGC
  discount, so shifting on top would double-count it.
* `sales-volume` (yearly units) replaces the comp-date velocity estimate.
* `genre` corroborates object class; a card identity resolved onto a Comic
  product is rejected as a resolution failure, not accepted as a price.
* Confidence is re-based on **match quality × specificity**, with a
  corroboration bonus from comps and a **staleness discount** — a 200-hour-old
  pool can no longer read as fresh conviction.
* `find_value_collisions()` / `log_value_collisions()` in `main.py` are an
  identity canary: distinct assets sharing a fair value to the cent is an
  arithmetic impossibility in a real market and now logs a warning. It keys on
  `identity_key`, so many copies of the *same* card are correctly silent.

### Verifying it against the live API

```text
Check PriceCharting.command      (code/probe_pricecharting.py)
```

Prints, per title: the extracted identity, the search phrase sent, every
candidate with its match score, which product was chosen, and the price read
off it. Run it before trusting a Max Bid on an unfamiliar product line. It
makes a handful of paced calls, writes nothing, and prints no credential.

### Guide coverage by category (measured 2026-07-26)

`Check PriceCharting Coverage.command` established which categories the guide
can lead on. The headline: **pricecharting.com does not carry sports cards.**
Across 381 candidates returned for '52 Mantle, '86 Jordan, '55 Koufax, '33
Goudey Ruth and Prizm Luka there was not one baseball or basketball card —
searches returned Funko POPs, LEGO sets, Magic cards and NBA 2K23 for PS5.

The fix is **sportscardspro.com**, PriceCharting's own sister site for sports
cards. Identical API (`/api/product`, `/api/products`), identical field names,
identical grade ladder, same 1-call-per-second limit — and **the existing
PriceCharting token is accepted there**, confirmed live. It landed all six
test cards at 90–100%:

```text
Mickey Mantle #311        | Baseball Cards 1952 Topps
Michael Jordan #57        | Basketball Cards 1986 Fleer
George Mikan #69          | Basketball Cards 1948 Bowman
Sandy Koufax #123         | Baseball Cards 1955 Topps
LeBron James #111         | Basketball Cards 2003 Topps Chrome
Luka Doncic [Silver Prizm] #280 | Basketball Cards 2018 Panini Prizm
```

`PriceGuide.quote()` now tries each host in `algorithm.guide_hosts` and keeps
the first confident answer. Deliberately NOT a "is this sports?" classifier —
that taxonomy would misfile Topps Chrome Disney the moment it saw "Topps".
Only rows that miss on the primary host cost a second lookup, and results are
cached permanently per host.

```text
guide can lead : vintage Pokemon, sealed games, comics, sports (via SportsCardsPro)
comps must carry: watches (not in either catalogue; excluded from Action anyway)
```

If sports volume grows, SportsCardsPro offers a **CSV download** for Legendary
subscribers — whole sets per request, refreshed daily — which sidesteps the
per-card rate limit entirely.

**Still worth pursuing:** eBay's Marketplace Insights API returns real sold
prices for the last 90 days, which beats any guide's modelled value. It is a
Limited Release API requiring eBay Partner Network signup, a Buy API
application with mockups, ~10 business days, and per-category whitelisting.
Approval is not guaranteed. File it as background work, not a dependency.

### What to expect

The workbook will be **quieter**. Rows that cannot be pinned to one asset are
now browse-only by design. That is the intended trade: fewer Action rows,
but ones where we know which card we are pricing.

**Not done, deliberately:** watch reference-family matching (Travel Time vs
World Time vs Chronograph) and part-token gating are specified in section 10
but not implemented — watches are excluded from Action anyway. The guide
pre-screen for below-floor rows was also deferred.

**One correction to a common assumption:** the PriceCharting API does **not**
return sold comps. Their docs are explicit — *"Historic prices and historic
sales are not supported."* Their website shows recent eBay sales; the API
gives current values by grade only. PriceCharting can lead on value, but it
cannot replace eBay comps as sold evidence, which is why the trust signal is
now "did we land the product?" rather than "how many comps do we have?".

---

## 2. Corrections to the previous handoff

| Previous handoff said | Verified reality |
|---|---|
| HEAD = `6492601`, 2 commits ahead of origin | HEAD = **`4034523`** (Jul 25 23:03), **3 ahead** |
| P0 #1 is "make the eBay API breaker atomic" | **Already fixed** in `4034523`. `base.py` uses `_api_lock` as both rate gate and atomic breaker-admission gate. Test: `test_parallel_api_failures_have_atomic_breaker_admission` (`test_fixes.py:1810`). Live proof: 422 API calls, 0 failures |
| eBay is "rate-limited/challenged" | The **Browse API lane is healthy**. Only the **sold-comps HTML** lane is cooling. Never conflate the two |
| Test suite = 114, then 140, then 142 | **156** |
| DB: `guide_cache 0`, `source_health 20`, `fair_history 6261`, 19.0 MB | `guide_cache 4575`, new `guide_product_cache 3377`, `source_health 164`, `fair_history 6438`, `observations 19513`, `alerts 44`, **20.8 MB** |
| `sites: ebay, yahoo_jp, goldin, heritage` | **5 sites** — `pristine` is enabled and live |
| PokemonTCG mismatch is an open P2 | **Fixed today** (§1.2) |
| Pristine shipping estimate is 0 and must be set | **Set to $15** (§1.4) |
| Latest run is the 11:54 full scan | Six BIN sweeps have run since (08:00 → 13:00), each writing a ~400 KB workbook with 48–50 kept rows |

Everything else in the previous handoff's architecture, economics, and
historical-bug sections was verified accurate and is preserved below.

---

## 3. Git

```text
4034523 (HEAD -> main)  Harden scan reliability and expose filter diagnostics  [Jul 25 23:03]
6492601                 feat: restore auction sources and optimize exit routes
8fb14fc                 feat: complete economics history and source readiness
0ce1780 (origin/main)   fix: price exact cards and validate bid routes
311f08f                 fix: gate learning and actions on trusted evidence
78db6b0                 fix: deduplicate comps by canonical listing identity
54ff23f                 security: redact credentials and support external secrets
a104a55 (v0.1.0, baseline-2026-07-25)  chore: establish card scanner baseline
```

`4034523` contains the atomic-breaker fix, the Filter Waterfall and
Research-Filtered diagnostics, and the first 2,464 lines of the old handoff.

Uncommitted working tree:

```text
 M code/main.py                       mode-aware caps, dedupe, orchestration, crash fix
 M code/models.py                     canonical_asset_id, insurance_on_buyer_fee
 M code/scrapers/__init__.py          pristine + alt registration
 M code/scrapers/base.py              capabilities, atomic admission
 M code/scrapers/ebay.py              Browse API pagination to the 500 ceiling
 M code/scrapers/fanatics_collect.py  Algolia removed; now an authorized-feed shim
 M code/scrapers/goldin.py            shipping tiers + 0.9% insurance
 M code/scrapers/heritage.py          parser canary
 M code/scrapers/point130.py          capability declaration
 M code/scrapers/yahoo_jp.py          parser canary
 M code/source_health.py              pristine/alt/fanatics rows
 M code/test_fixes.py                 156 tests
 M code/valuation/price_guide.py      request control, product cache, DB path, ptcg gate
 M docs/FEATURES.md  docs/README.md  secrets.example.yaml
 M "sol extra high handoff.md"
?? code/scrapers/alt.py
?? code/scrapers/authorized_feed.py
?? code/scrapers/pristine.py
?? docs/PLATFORM_ACCESS.md
?? opus_high_handoff.md               (this file)
?? solextrahighhandoff.md             (duplicate hardlink - see §1.5)
```

`config.yaml` is git-ignored and carries live changes: Pristine enabled and its
costs, Goldin landed-cost settings, PriceCharting and eBay sold pacing, the eBay
500 cap, and today's BIN overlay.

When Andrew asks to commit: inspect `git diff`, include the four new source
files, rerun the 156-test suite, and use one clear message covering request
control, platform expansion, trust gates, eBay pagination, and today's four
fixes. Then push all three commits to `origin/main`.

---

## 4. User, goal, and working style

Andrew is a former ETF trader deploying substantial working capital into
collectible inventory. Main target is profitable resale, with a separate
personal-collection grail strategy.

Categories: graded vintage Pokemon; vintage and premium sports cards; sealed or
graded vintage video games; selected high-end watches; selected comics and
pop-culture material; and personal grails that matter regardless of profit.

This is a trading decision system, not a scraper. It must:

1. search multiple marketplaces for auctions and fixed-price listings;
2. build a defensible fair value from sold comps and price guides;
3. model where auctions actually close, not the displayed bid;
4. include every material acquisition cost;
5. select the best eligible resale venue by expected net proceeds;
6. calculate expected profit, ROI, liquidity, Max Bid, and Breakeven;
7. produce one concise workbook per run, led by a Today decision list;
8. send only high-quality Telegram alerts and a daily digest;
9. learn from real closes without contaminating the model.

Andrew is not a developer. His interface is: double-clicking `.command` files,
opening the `.xlsx`, reading `logs/scan.log` when something is wrong, editing
`config.yaml`, and pasting suspicious report rows back into a session.

Communicate in trader language: edge, return, turnover, capital velocity,
mark-to-market, evidence quality, risk, bid ceilings. Lead with the outcome. If
Andrew says a row looks nonsensical, assume it exposes a real root-cause bug —
reproduce that exact row as a regression test before changing general logic.

---

## 5. Non-negotiable safety rules

These exist because violating them previously produced materially wrong buys.

**5.1 Credential hygiene.** Never print, paste, commit, or repeat API keys,
Telegram credentials, tokens, or secret-bearing URLs. `config.yaml` is ignored
and currently holds local credentials. Precedence: inline `config.yaml`, then
ignored `secrets.yaml`, then environment variables (which win).

```text
CARD_SCANNER_EBAY_CLIENT_ID          CARD_SCANNER_TELEGRAM_BOT_TOKEN
CARD_SCANNER_EBAY_CLIENT_SECRET      CARD_SCANNER_TELEGRAM_CHAT_ID
CARD_SCANNER_PRICECHARTING_TOKEN     CARD_SCANNER_SECRETS_FILE
CARD_SCANNER_POKEMONTCG_API_KEY      CARD_SCANNER_FANATICS_ENDPOINT
CARD_SCANNER_FANATICS_APP_ID         CARD_SCANNER_FANATICS_ACCESS_TOKEN
CARD_SCANNER_FANATICS_SEARCH_KEY     CARD_SCANNER_ALT_ENDPOINT
                                     CARD_SCANNER_ALT_ACCESS_TOKEN
```

**5.2 Test before live network work.** Every live scan runs the full suite in a
subprocess first and fails closed. Exit 2 means tests failed and no live work
should happen.

**5.3 Do not weaken evidence gates to make the workbook fuller.** An empty
workbook is safer than confident nonsense. The remedy for a quiet search is
better targeted queries or better comps, never looser matching.

**5.4 A transport success is not a parser success.** HTTP 200 with a changed
schema is a failed source. Goldin, Heritage, Fanatics, Pristine, and
Yahoo/Buyee write explicit parser outcomes into the shared API statistics.

**5.5 Never mix incompatible economics.**
- PSA Vault's 0% acquisition-tax treatment requires the item stay in the vault.
- A vault fee *replaces* the marketplace fee; it does not stack.
- A buyer premium with a minimum is nonlinear — use `Listing.buyer_fee()` and
  the correct inverse.
- Max Bid is a target-return ceiling. Breakeven is a zero-profit wall.
- A zero-bid hybrid auction is priced at its known Buy It Now, not the opener.

**5.6 Browsing rows and actionable rows are different products.** Questionable
values may remain in category/research sheets, but must not enter Today,
Action, alerts, digest, fair-history trends, portfolio marks, or learner
training unless they pass the centralized gate.

---

## 6. Repository map

```text
ebay opportunities/
  Run Scan.command  Run BIN Sweep.command  Run Tests.command
  Setup Telegram.command  Test Alerts.command  Test Comps.command
  Fix Mac Sleep.command
  opus_high_handoff.md          <- this file, current
  sol extra high handoff.md     <- prior, superseded
  solextrahighhandoff.md        <- duplicate hardlink of the above
  HANDOFF.md  fable_handoff.md  NEXT_RUN_CHECKLIST.md   <- historical
  config.yaml                   <- ignored; live settings and credentials
  secrets.yaml / secrets.example.yaml
  .venv/

  code/
    main.py models.py economics.py quality.py report.py db.py learner.py
    closer.py alerts.py digest.py grails.py portfolio.py misspell.py
    textutil.py security.py source_health.py paths.py demo_data.py
    test_fixes.py
    scrapers/  base.py ebay.py goldin.py heritage.py yahoo_jp.py
               pristine.py alt.py fanatics_collect.py authorized_feed.py
               point130.py __init__.py
    valuation/ engine.py comps.py price_guide.py __init__.py

  database/  history.db + dated migration backups
  model/     learned_params.json model.pkl
  portfolio/ portfolio.csv
  reports/   Opp Runs/  BIN runs/
  logs/      scan.log (+ .1 .2 .3)
  state/     .scan.lock .breaker_state.json .cookies_<site>.json
  setup/     requirements.txt
  test results/  docs/
```

`code/paths.py` is the source of truth for folder names. **Everything resolves
against the folder containing `config.yaml`, never the shell's cwd.** As of
today that now includes the price guide's database connection (§1.1).

Do not move the project into an iCloud-evicted Desktop location. The folder once
disappeared during a macOS/iCloud Desktop migration.

Environment: macOS 26.5.2 (25F84), Python 3.9.6. Key packages: `curl_cffi`
0.13.0 (browser-like TLS/HTTP2 fingerprint — plain `requests` gets blocked),
`openpyxl` 3.1.5, `scikit-learn` 1.6.1, `beautifulsoup4` 4.15.0, `PyYAML` 6.0.3.

---

## 7. How to run it

```bash
cd "/Users/alekas/Desktop/ebay opportunities"

# full scan (or double-click "Run Scan.command")
caffeinate -im .venv/bin/python -B code/main.py -c config.yaml \
  -o "reports/Opp Runs/opportunities_$(date +%Y-%m-%d_%H.%M).xlsx"

# BIN sweep (or double-click "Run BIN Sweep.command")
caffeinate -im .venv/bin/python -B code/main.py --mode bin -c config.yaml \
  -o "reports/BIN runs/bin_sweep_$(date +%Y-%m-%d_%H.%M).xlsx"

# tests (or double-click "Run Tests.command")
.venv/bin/python -m unittest discover -s code -p "test_*.py"
.venv/bin/python -B code/main.py --demo -o /tmp/scanner_selfcheck.xlsx

# calibration
.venv/bin/python -B code/main.py --calibrate
```

CLI: `--mode all|auctions|bin`, `--demo`, `--calibrate`, `--skip-self-test`
(emergency only), `-c`, `-o`, `-v`.

Exit codes: `0` success, or a BIN sweep that deliberately skipped because
another scan owns the lock. `1` no actionable/live inventory, or a runtime or
source failure. `2` regression self-test failed before scanning. **An exit 1
with no workbook is not necessarily "no bargains"** — read `logs/scan.log` and
Source Health to see whether sources failed.

**Run lock** (`state/.scan.lock`, contents `<pid> <mode> <started>`): a BIN
sweep that sees a holder logs it and exits 0. A full/manual scan that sees a
holder sends SIGTERM, waits up to 20s, then SIGKILL, and takes over. Demo and
calibration bypass the lock. Andrew chose this because frequent slow sweeps
previously made manual scans almost impossible to start.

---

## 8. Runtime architecture

### 8.1 Boot

`code/main.py` parses CLI args, resolves all paths from the config location,
loads secrets/environment overlays, initializes rotating logging, runs the
156-test **subprocess** self-test (a subprocess so mocked modules, temp
databases, and patched logging cannot leak into the live process), then takes
the process lock.

### 8.2 `run_live` phases

Network work and SQLite writes are deliberately separated.

**Phase A — main-thread cache planning.** Open the DB, instantiate configured
scrapers, read cached comp pools per watchlist query, add grail discovery
queries on a full scan, prefetch one eBay OAuth token.

**Phase B — parallel network fetch.** `ThreadPoolExecutor`, 10 workers. Per
query: fetch comps if cache is absent; search auctions on every configured
auction connector; search fixed-price on every configured fixed connector for
`all` and `bin`; international sources for priority queries; typo variants for
priority queries on full scans. HTML requests are serialized per scraper
instance by a lock; API requests are concurrent but pass an atomic admission
gate. One query failure is logged and does not abort the run.

**Phase C — persistence and relevance.** Save new comps, fall back to stale
comps when live refresh is blocked, remove comps with excluded keywords, dedupe
live listings by canonical identity, apply grade/language/variant/subject/
title-match/cap/max-price guards, set priority/discovery/category, set
`resale_channel` from watchlist or default `auto`, tag grails, build live ask
pools.

**Phase D — exact-card targeted comps.** `plan_targeted_comp_queries`
deduplicates and prioritizes exact queries. 20 per full scan, 6 per BIN sweep,
60 results each, at most four targeted workers. Cached exact pools preferred.
Thin or missing exact pools trigger a **quarantined** mixed-pool fallback.

**Phase E — valuation and trusted persistence.** Per listing: call
`ValuationEngine.evaluate`, select specific vs broad comp pool, score
acquisition and exit economics, persist only evidence passing
`quality.evidence_rejection`, record a query-level trusted fair once per run,
record observations for later close matching.

### 8.3 After collection

`main()` captures and persists Source Health; applies per-category fair-value
floors; removes impossible ROI rows; applies collecting standards; applies
output/actionability rules; trims to `max_rows` (never trimming grails); builds
portfolio marks; writes the workbook; sends alerts; sends the full-scan digest;
settles recently ended auctions; retrains or reports cold learner state; logs
the top row; and always writes the API summary and duration footer.

### 8.4 Connector capabilities

`BaseScraper.capabilities` / `supports()`. Lanes: `auctions`, `fixed`, `sold`.

```text
eBay              auctions, fixed, sold
Goldin            auctions
Heritage          auctions
Yahoo/Buyee       auctions
Pristine          auctions
Fanatics Collect  auctions, fixed   (disabled - no authorized access)
ALT               auctions, fixed   (disabled - no authorized access)
130point          sold              (disabled - Cloudflare)
```

`main._search_marketplaces()` is the single orchestration point. **Consequence
worth knowing:** BIN mode calls only `fixed` connectors, and only eBay declares
`fixed`. So a BIN sweep shows Goldin/Heritage/Pristine/Yahoo as
`idle — no network call`. That is correct, not an outage.

### 8.5 Cross-platform dedupe

`_listing_identity()` priority: explicit source-independent physical asset
identity (`canonical_asset_id`), then source-namespaced native listing ID, then
source-namespaced canonicalized URL. If the same physical asset is cross-listed,
the route with the lower known landed cost is kept.

**Title similarity is never used for physical dedupe.** Two copies with
identical card/set/grade titles remain two listings. Authorized-feed connectors
derive IDs like `psa:12345678` only when both a grader and certificate number
are supplied.

---

## 9. Immediate operational actions

### 9.1 Move the BIN sweep to hourly — **needs Andrew**

The mode-aware cap (§1.3) should bring sweeps back to roughly 4–6 minutes, but
Andrew asked for hourly cadence as well. That schedule lives on the Mac and this
environment cannot reach it. To change it:

```bash
crontab -l                  # confirm the current schedule first
crontab -e                  # then change the BIN sweep line from */30 to hourly
```

The line should go from `*/30 * * * * ...` to `0 * * * * ...`. If the sweep is
run by launchd instead of cron, the plist will be under
`~/Library/LaunchAgents/` and the interval is `StartCalendarInterval` or
`StartInterval`.

Note: the 13:00 sweep was still holding the lock 58 minutes in when this was
written. It loaded the pre-fix code, so it will not benefit from the new cap.
Either let it finish or start a full scan (which will SIGTERM it by design).

### 9.2 Next code change: guide pre-screen

91% of valued rows (3,813 of 4,209) are discarded at the fair-value floor, many
after a PriceCharting call. The safe version: skip the guide when comps alone
are confidently far below the category floor (`n_comps >= min_comps` **and**
`comps_value < 50%` of floor) — the guide cannot rescue those rows anyway. This
is the biggest remaining runtime and API-budget win. Deferred by Andrew to its
own change with its own tests.

### 9.3 Let the eBay sold lane recover

`ebay/html` is cooling until ~14:58 CDT. The comp cache is 71h old against a 48h
limit and has not grown at all. After the cooldown, refresh gradually under the
existing 10-second one-at-a-time pacing. **Do not** delete breaker state to force
requests. Do not promote stale broad comps to Action merely because
PriceCharting returns a compatible-looking product.

---

## 10. The real bottleneck: valuation identity

Verified today: `code/quality.py` contains only `NOTE_BLOCKERS`
(ASK-BASED / MIXED POOL / SUSPICIOUS), `tradeability_rejection`, and
`evidence_rejection`. There is **no** `identity_specificity` or
`exact_product_match` attestation, and no object-class classifier anywhere in
`code/`. The following work is **not started**.

### 10.1 Evidence from the 11:54 workbook

**Disney parallels collapsed into one value.** Today and Action contained two
auctions, both still live and ending that evening:

```text
BID?  2023 Topps Chrome Disney 100 Cinderella Pink Refractor /399 PSA 10
      $415 + $4.99 | 21 bids | fair $1,069.60 | max bid $859 | breakeven $989

BID?  2023 Topps Chrome Disney 100 Escape from a Plane /10 PSA 10
      $456 + $4.99 | 18 bids | fair $1,069.60 | max bid $859 | breakeven $989
```

A `/399` and a `/10` received the identical $1,069.60 fair value from the same
six-row `Disney Chrome 2023 PSA 10` pool (comps $950, guide $1,134, blend
w_comps=0.35, agreement 84%). Variants from `/5` through `/399` all inherited
it. These escaped the mixed-pool quarantine because the two evidence sources
agreed **at the wrong level of specificity**. Do not bid these numbers without
independent exact-card verification.

**Watch false positives.** The Watches tab priced a Patek Ref 1593 *dial for
restoration*, an AP Royal Oak *dial*, and a Patek *buckle* as if they were
complete watches; and mismatched model families (Calatrava Travel Time valued
from a World Time pool; Royal Oak 15400 under Royal Oak Chronograph). Only the
buckle was flagged mixed pool. Watches are excluded from Action by design, which
limited harm — but the Watches sheet is not purchase-grade.

**Object-class collisions.** The `Other` tab assigned ~$2,821 under
`Superman 1940` to a PSA card, a novelty coupon, a wax-pack wrapper, Action
Comics #22, and an $18 modern McFarlane figure. Several were flagged disputed
or suspicious so the tradeability gate kept them out of Action — the quarantine
works, but the identity layer should prevent the valuation existing at all.

**Discovery.** The broad `Upper Deck Authenticated Signed Jersey` pool assigned
$1,085 to full signed jerseys, jersey autograph cards, game-used jersey cards,
golf-club autograph cards, and modern hockey cards alike. All 21 Discovery rows
were correctly blocked from decisions. Keep that protection.

### 10.2 Required sequence

1. **Object-class identity before valuation.** Classify complete watch, watch
   component, trading card, comic, wrapper, pack, coupon, figure/toy, jersey,
   jersey card, video game, generic memorabilia. A listing must match its
   query's object class or become browse-only.
2. **Exact card identity for decision rows.** Extract set/year, card number,
   subject, parallel/color, autograph/relic state, serial denominator, grader,
   effective grade. `/399` and `/10` cannot share a decision-grade pool. Card
   number and parallel conflicts hard-block Action.
3. **Broad-pool quarantine.** Query-level guide/comps without exact identity may
   populate Discovery or Grails, never Action/Today/alerts/learning/portfolio.
   Add an explicit `identity_specificity` / `exact_product_match` attestation to
   valuation trust. **Agreement between two broad sources is not evidence.**
4. **Watch reference and component gates.** Reject or browse-quarantine titles
   with `dial`, `buckle`, `strap`, `band`, `bracelet`, `parts`, `movement`,
   `case`, `bezel`, `hands`, `for restoration` unless the query asks for that
   component. Match reference numbers. Treat Travel Time, World Time, Day-Date
   Moonphase, and Chronograph as different families. Never route a watch through
   PSA Vault.
5. **Repair secondary sources.** Yahoo/Buyee parser; Heritage stability after
   cooldown; authorized Fanatics/ALT access via `docs/PLATFORM_ACCESS.md`.

**Do not** begin by lowering fair-value floors or disabling the ROI ceiling.
Those two gates removed 3,897 rows in the last full run and are doing real work.

---

## 11. Data model

**`Listing`** (`code/models.py`) core fields: `site, title, url, current_price,
shipping, bid_count, end_time, image_url, listing_id, query, priority,
discovery, misspell_from, listing_type, best_offer, has_buy_now, buy_now_price,
grail, grail_score, created_at, currency, marketplace, seller_feedback`.
Exit/category: `resale_channel` (default `auto`), `category`,
`canonical_asset_id`. Acquisition costs: `buyer_fees, buyer_fee_rate,
minimum_buyer_fee, international_shipping, insurance_rate, import_duty_rate,
fx_spread_rate, insurance_on_buyer_fee`.

```text
buyer_fee(P) = max(P * buyer_fee_rate, minimum_buyer_fee)

L(P) = P * (1 + insurance_rate + import_duty_rate + fx_spread_rate)
       + shipping + buyer_fees + international_shipping + buyer_fee(P)
```

The inverse `item_price_for_landed_cost()` handles both the percentage branch
and the fixed-minimum branch. **Do not simplify it to one linear rate.**

**`SoldComp`**: `title, price, sold_date, url, site`. Price includes shipping
when known.

**`Valuation`**: `fair_value, comps_value, guide_value, n_comps, dispersion,
confidence, expected_cost, expected_value, edge_now, capture, trend_30d, roi,
sales_per_month, annualized_roi, opportunity_score`; exit results
`resale_channel, resale_fee_rate, net_proceeds, exit_advantage`; trust state
`regraded, disputed, notes, audit_notes`. `notes` are human decision notes;
`audit_notes` are model diagnostics hidden in Excel column AD.

**`Opportunity`** pairs one `Listing` with one `Valuation`.

---

## 12. Marketplace adapters

### 12.1 eBay (`code/scrapers/ebay.py`)

Official Browse API for auctions and fixed price across US/GB/DE, with FX
conversion, seller feedback, created/end times, hybrid auction+BIN, and an HTML
fallback. Sold comps come from eBay sold-listing HTML with Chrome/TLS
impersonation, challenge-page detection, and cached fallback.

**Pagination.** eBay allows 1–200 items per page, max result set 10,000, max
offset 9,999, default app quota 5,000 calls/day. The connector keeps a constant
200-item page size, advances from `offset + limit`, follows `next`, and stops at
the configured ceiling, an empty page, an API failure, or non-advancing
pagination. The final page is sliced locally to the exact ceiling.

```text
full scan: page 1 limit=200 offset=0 | page 2 offset=200 | page 3 offset=400
           -> local slice to 500
BIN sweep: ceiling 100 (as of today)
```

Ceilings apply **per marketplace and per lane**. A priority query across
US/GB/DE can therefore reach 1,500 auctions + 1,500 fixed on a full scan. The
cap does not change sold-comp HTML limits, `targeted_comp_max_results: 60`,
misspelling variants (20 per variant/lane), or the 40 default for other sites.

### 12.2 Goldin (`code/scrapers/goldin.py`)

```text
POST https://d1wu47wucybvr3.cloudfront.net/api/lots_v2
Origin: https://goldin.co     Content-Type: application/json
{"search": {"queryType": "Search", "keyword": "<query>", "size": 50, "from": 0}}
schema: searchalgolia.lots[]
fields: status title current_price min_bid_price number_of_bids
        end_timestamp meta_slug lot_id buyer_premium
url:    https://goldin.co/item/<meta_slug>
```

Landed cost: lot-provided buyer premium (observed 22%), configured $19 minimum,
$6 single-card shipping under $1,000 / $19 at or above, $19 floor for non-card
lots, and 0.9% insurance on hammer plus premium. Example: $2,500 hammer →
$3,096.45 ($2,500 + $550 + $27.45 + $19).

**Limitation:** shipping is chosen from the listing's current price tier and
then treated as fixed by the generic inverse. Crossing the $1,000 threshold
changes shipping by $13; large or heavy non-card lots can exceed the $19 floor.
Verify near-threshold bid ceilings and non-card shipping manually when material.

Exit: Goldin Marketplace 8.3% (promotional/current — reverify periodically),
auto only for eligible professionally graded Pokemon/sports/games, $100 minimum.

### 12.3 Heritage (`code/scrapers/heritage.py`)

```text
https://sports.ha.com/c/search/results.zx
  N=0  Nty=1  Ntt=<query>  dept=3923  mode=live  layout=list
```

The parser avoids brittle CSS classes: it finds `/itm/` anchors, walks parents
until a `Current Bid:` block appears, extracts the dollar bid, deduplicates
hrefs, and records a canary. Buyer economics 22% with a $29 minimum. Exit terms
are not public enough to trust automatically — present as a manual channel at a
provisional 20% with `auto_enabled: false`. Narrow Pokemon queries legitimately
return zero; that is not a parser failure.

### 12.4 Yahoo Japan / Buyee (`code/scrapers/yahoo_jp.py`) — BROKEN

```text
https://buyee.jp/item/search/query/<translated-query>?translationType=98
old schema: li.itemCard, .itemCard__itemName,
            a[href*="/jdirectitems/auction/"], a[href*="/paypayfleamarket/item/"],
            .g-priceDetails__item .g-price
```

Requests return HTML, no `li.itemCard` nodes appear, the canary records
`yahoo_jp/parse: failed`, and every priority query returns zero.

**Do not guess a new selector.** Inspect current browser network traffic and
determine whether inventory moved to an XHR endpoint, HTML needs a
session/cookie/region, markup changed, the page is an empty SPA shell, or Buyee
is selectively blocking automation. Implement a parser fixture first, then a
live smoke test.

The Japan landed-cost model is already complete and should be preserved: $10
proxy fee, $8 domestic shipping, $35 international, 1% insurance, 3% FX spread,
15% import-duty planning assumption, JPY/USD 0.0063. These are conservative
configurable estimates, not customs advice.

### 12.5 Authorized feeds — Fanatics Collect and ALT

`code/scrapers/authorized_feed.py` is the shared implementation; `fanatics_collect.py`
and `alt.py` are thin subclasses. The old Algolia adapter was removed — it
carried a rotated public app ID and instructions to extract a browser search
key, which is not an approved integration model.

```json
{"items": [{"title": "...", "url": "...", "current_price": 100}]}
```

Endpoint calls use GET params `q`, `type` (`auction`|`fixed`), `limit`. Bearer
tokens, configurable API-key headers, local relative file paths, and environment
overrides are supported. **Zero network requests occur unless `authorized: true`
and `endpoint` are both configured**; a local `feed_file` never contacts the
platform. Defaults: 20% auction buyer premium, none on fixed; feed fields may
override. Full contract in `docs/PLATFORM_ACCESS.md`.

Both remain absent from `sites` pending access. Do not invent endpoints, use
leaked keys, or scrape protected surfaces.

### 12.6 Pristine (`code/scrapers/pristine.py`)

```text
GET https://www.pristineauction.com/auction/search/
    term, category, sort_method=ending-soonest, per_page (15|30|60)
```

Live markup verified 2026-07-26:

```html
<div class="row product" aria-label="Auction item"
     data-pristine-product-venue-id="12868123" data-pristine-title="...">
  <a class="title" href="/a12868123-...">...</a>
  <p class="high-bid" data-high-bid="6.35"></p>
  <span class="end-time" data-pristine-end-time="1785121200"></span>
</div>
```

Shared `curl_cffi` Chrome impersonation, serialized polite HTML lane, breaker,
cookies, 2.5s base delay. Published 17% buyer premium plus the configured $15
shipping estimate (§1.4). A legitimate `No results found` page is parser-healthy.

### 12.7 130point (`code/scrapers/point130.py`) — disabled

`GET https://130point.com/api/search/html?q=<query>&saleType=sold`, parsing
`a[data-sold-result]` including hidden accepted Best Offer prices and real sale
dates. Cloudflare currently blocks it. Its unique value is **real sold dates for
velocity** — without it, eBay comp dates are coarse fetch dates and Sales/mo and
annualized ROI are useful but imperfect. Revival requires a legitimate API, a
durable authenticated session, or formally accepting eBay+PriceCharting as the
comp stack. Do not hammer it.

### 12.8 Price guides (`code/valuation/price_guide.py`)

PriceCharting is configured and paid. PokemonTCG.io has no key and, as of
today, makes no calls (§1.2).

Behavior: guide cache versioning invalidates stale valuation-rule results;
PriceCharting is skipped when there are ≥8 strong comps; 429 `Retry-After` is
respected; failures never cache `NULL`; every endpoint backs off after three
consecutive failures; grades are priced at **PSA-equivalent** grade, not the raw
slab label; half grades interpolate without rounding upward; missing lower-grade
rungs return no quote rather than inflating.

One serialized wire lane with ≥1.0s pacing (PriceCharting's paid API is one call
per second; configuration cannot violate it). The last full run made 635 calls
with zero failures.

**Known sharp edge:** PriceCharting field names (`loose-price`, `cib-price`,
`new-price`, `graded-price`, `box-only-price`, `manual-only-price`) are
overloaded for graded-card rungs in this integration, so grade-routing logs can
look strange without being wrong. The dangerous failure is product identity: the
correct grade rung on the wrong product is still a bad valuation.

---

## 13. Network layer and breakers (`code/scrapers/base.py`)

Prefers `curl_cffi.requests.Session(impersonate="chrome")`, falls back to
`requests.Session`. The fallback retries selected 5xx but **never** a 429. HTML
timeout 12s, API timeout 30s. HTML lanes serialized; API lanes concurrent behind
an atomic admission gate.

```text
API_STATS[(endpoint, outcome)]   outcome = ok | failed | skipped
state/.breaker_state.json        per <site>/api and <site>/html
state/.cookies_<site>.json       persistent cookie jars

trip after 3 consecutive failures    hard challenge backoff after 3 challenges
base cross-run cooldown 30 minutes   exponential escalation, max 24 hours
strikes reset after a quiet 24 hours
```

Every challenge counts even if a retry clears it; hitting the threshold wipes
the cookie jar and persists a trip; new runs refuse to contact a lane until the
cooldown expires; one lane can cool while another stays healthy.

**Atomic admission (fixed in `4034523`).** `self._api_lock` is both the rate gate
and the breaker-admission gate: a worker re-checks `lane_tripped()` while
holding the lock, so exactly three consecutive failures can reach the wire. This
replaced a race where ~10 workers passed a bare pre-check simultaneously and a
run logged 30 API 429s after a nominal three-failure trip. Regression test:
`test_parallel_api_failures_have_atomic_breaker_admission`. **Do not "solve"
future variants of this by raising the failure threshold.**

Current breaker state:

```text
ebay/html      5 strikes   until 2026-07-26 19:58 UTC (14:58 CDT)
ebay/api       3 strikes   until 2026-07-26 05:22 UTC   EXPIRED - healthy
heritage/html  3 strikes   until 2026-07-26 18:49 UTC (13:49 CDT)  EXPIRED
130point/html  2 strikes   EXPIRED (source disabled anyway)
```

---

## 14. Comp hygiene (`code/valuation/comps.py`)

Base estimate: title-match floor 0.55, MAD outlier trim k=3, recency-weighted
median, 30-day comp half-life.

A comp is rejected for: effective grade mismatch; language mismatch;
holo/non-holo/reverse mismatch; 1st Edition/unlimited/shadowless mismatch;
Topsun original vs Topsun VS; sealed/CIB/loose mismatch; year mismatch;
card-number mismatch; wrong subject; excluded keywords; too-low fuzzy title
match.

**Grade normalization.** PSA stays PSA. CGC/BGS/SGC/BVG use Andrew's
one-full-grade-down rule against PSA. Effective grades floor at 1. WATA and VGA
are video-game graders and do **not** receive the PSA card shift. Legacy SGC
100-point labels are normalized. Impossible tokens like `CGC 85` are rejected
and treated conservatively as ungraded. Ungraded is assumed PSA 5 for matching
and valuation — a planning convention, not a claim that every raw card grades 5.

**Subject extraction.** `subject_candidates()` removes set/grade/context/
marketing vocabulary, uses real early title tokens, defaults to two subject
tokens, and handles accented Latin characters while preserving Japanese
combining marks. Set-wide queries previously injected seller adjectives like
"investment beautiful centering" as the subject. **Do not replace
`textutil.fold()` with naive NFKD stripping** — it once corrupted Japanese
dakuten.

**Exact card number.** Once a listing exposes a card number absent from the
broad query, the engine creates a specific query with card number and grade.
`number_conflict` is deliberately asymmetric: if a query names a number,
unnumbered comps are rejected too. With fewer than three exact comps: use the
quarantined broad fallback, add a MIXED POOL note, cap confidence at 0.25, and
bar the row from action/alerts/history/learning. Do not weaken this to make
Jordan/Ruth searches look busier — add explicit card-number watchlist queries.

---

## 15. Fair value and scoring (`code/valuation/engine.py`)

**Fair value order:** sold comps + guide blend; sold comps only; guide only with
a 5% haircut; ask-based estimate; no value. Comps weight grows with sample size,
saturates near eight comps, shrinks with dispersion, and has a 35% minimum when
both exist.

Ask-based estimates require ≥3 live fixed-price asks, MAD trim, lower quartile,
10% haircut, confidence capped ~0.35, and are **explicitly not tradeable**.

If comps and guide differ by more than roughly 4x: `disputed=True`, confidence
capped at 0.30, VALUE DISPUTED note, excluded from decision/evidence outputs.

**Auction expected close.** Base settle anchor `resale_value * 0.92`
(cold-start). Displayed bid gets a 2% proxy lift per bid capped at 15%; expected
close interpolates toward the anchor on a 24-hour time constant; in the last six
hours it is floored at 75% of the anchor; it never falls below the current bid;
a hybrid never exceeds a known Buy It Now. A learned band/global ratio can
override 0.92 when trustworthy data exists, and deployed ML can override the
parametric ratio.

**BIN.** Expected item price is the ask. Fresh listing capture is high and
decays on a 24-hour half-life; unknown age gets conservative capture. Best Offer
note suggests ~80% of resale value, capped by the ask.

**Capture.** Auction capture decays with time remaining on a 12-hour half-life
with a floor. Too-good-to-be-true price and low seller feedback reduce capture.

**Confidence** combines sample size, comp tightness, title-match quality, and
comps/guide agreement, then scales by listing/query match. Japanese and
typo-discovery listings get special handling but stay flagged for verification.

```text
opportunity_score = min(roi, 1.0) * confidence * capture
```

Annualized ROI is visible but deliberately **not** in the sort score. Andrew
deferred velocity-weighted ranking until he trusts Sales/mo.

---

## 16. Acquisition and exit economics (`code/economics.py`)

Buy-side planning tax 8%. Tax-free marketplaces: `YAHOO_JP`, `PAYPAY_JP`.

`ExitRoute`: `channel, fee_rate, fixed_cost, net_proceeds, advantage_vs_ebay`.
Each configured route may use `fee_rate, fee_tiers, fixed_cost, min_value,
max_value, categories, requires_graded, enabled, auto_enabled`.

When `resale_channel == "auto"`: enumerate routes, apply eligibility, evaluate
net proceeds, choose the maximum, calculate advantage against configured eBay
economics. An explicit watchlist or portfolio channel is honored as a manual
override. Legacy configs without `resale_channels` remain eBay-only in auto mode.

Currently configured (under `algorithm.resale_channels`):

```text
eBay              13.25%
Goldin             8.30%  min $100, Pokemon/Sports/Video Games, requires graded, auto
Heritage          20.00%  manual only
Fanatics Collect  15.00%  manual only
PSA Vault         eligible eBay acquisitions at landed cost >= $500;
                  0% modeled acquisition tax; 7% all-in exit;
                  EXCLUSIVE route once vault tax economics apply
```

ALT has no resale channel configured yet.

The selected route drives Edge Now, expected auction/BIN EV, expected net
proceeds, ROI, Today Max Bid, Today Breakeven, and open Portfolio marks. **Do
not reintroduce separate sell-fee math in report or portfolio code.**

---

## 17. Quality gates (`code/quality.py`)

**Tradeability gate** — hard blockers: disputed value, suspicious price, mixed
comp pool, ask-based estimate, discovery query, no fair value. Action, Today,
alerts, and digest share it. Category tabs retain rows for inspection.

**Evidence gate** — stricter, for history and learner: everything above plus
regraded/per-listing value, collection-standard failure, fair value below floor,
too few comps, too-low confidence. Legacy `fair_history` rows carry
`trusted=NULL`; only `trusted=1` drives Movers and Portfolio fallback marks.

**Output rules.** Grails are always retained. Graded Pokemon at effective PSA 5
or below are dropped under current config. Negative EV and negative ROI are
dropped. A pure zero-bid auction is dropped; a hybrid is exempt only when Buy It
Now is known; Yahoo is exempt from the bid-count rule because Buyee does not
expose it.

`output_ok` is module-level **on purpose**. Do not move it back inside `main()`
— a function-local `_category` import once shadowed the module global and killed
every run for hours.

**Collecting standards.** Pokemon must show 1st Edition / First Edition / 1st Ed
/ No Rarity, with named vintage exemptions (Topsun, Carddass, Movie Promo, No
Rarity); grails exempt. Video games must be sealed/new-in-box/shrinkwrapped or
professionally graded, with a $250 category floor instead of $1,000. Watches are
visible in their category sheet but excluded from Action, profit alerts, and
digest because modifiers, box/papers, and authenticity make automated valuation
untrustworthy.

---

## 18. Excel report (`code/report.py`)

Sheets in order, empty optional sheets omitted: `Today`, `Action`,
`Filter Waterfall`, `Research-Filtered`, `Pokemon Cards`, `Sports Cards`,
`Video Games`, `Watches`, `Other`, `Grails`, `Crossover`, `Portfolio`,
`Source Health`, `Discovery`, `Movers`, `About`.

**Today** — end-of-day decision list: auctions ending within 24h sorted by
deadline, fresh BINs within 24h sorted by score, EV floor $75, confidence floor
25%, centralized tradeability gate, Decide label BID? / BUY? / OFFER?. Columns
include Best Exit, Net Proceeds, vs eBay, Max Bid, Breakeven. Max Bid leaves 15%
target ROI; Breakeven is gray and represents zero profit.

**Action and category sheets** columns:

```text
Rank, Pri, Type, Site, Title, Query, Price, Ship, Bids, Timing, Fair Value,
Trend 30d, Comps Val, Guide Val, #Comps, Exp Cost, Expected Value, Edge Now,
ROI, Sales/mo, Ann ROI, Capture, Conf, Score, Best Exit, Exit Fee,
Net Proceeds, vs eBay, Notes, Model Detail
```

M:P is a hidden/grouped valuation audit block. Notes is AC. Model Detail is
hidden AD (older docs saying Z are stale). Action excludes watches, applies the
tradeability gate, collapses duplicate cards, and always includes the top 50
ranked tradeable rows plus positive-EV/soon rows. Category sheets are the full
uncollapsed research book and can legitimately show rows Action excludes.

**Dedupe** collapses rows only when: same query, same listing type, same
effective grade, overlapping subject tokens, title match ≥0.6, no variant
conflict. Auctions and BINs never merge. The retained row is best EV, then
cheapest. Notes show `+N more listing(s) ...`.

**Crossover** allowlist: graders CGC/BGS/SGC/BVG, categories Pokemon Cards and
Sports Cards. WATA/VGA games cannot enter; disputed/nontradeable values cannot
enter. Default PSA fee tiers ≤$500 $25, ≤$1,500 $75, ≤$2,500 $150, above $300.
Regrade Profit is modeled edge minus grading fee — the risk that a crossover
returns below the assumed shifted grade is real.

**Source Health** columns: Source, Status, OK, Failed, Skipped, Freshness, Mode,
Checked, Detail. Statuses: healthy, idle, disabled, degraded, cooling, failing,
stale, empty. Parser calls contribute separately, so `HTTP ok + parse failed`
becomes degraded rather than healthy.

**Portfolio** — `portfolio/portfolio.csv` columns: `date_bought, description,
query, cost_basis, date_sold, sale_proceeds, resale_channel, notes`. Open
positions mark from this run's trustworthy fair values, fall back to the latest
`trusted=1` fair history, optimize exit unless manually pinned, show Best Exit,
and calculate net liquidation value, P&L, return, CAGR, and totals. Closed
positions use recorded net sale proceeds.

**Grails display cap.** `_grails_tab` shows only the cheapest five listings per
grail. All grail candidates are kept internally, which is why kept-row counts
exceed visible rows.

---

## 19. Alerts, digest, grails, closer

**Profit alerts** (`code/alerts.py`): `edge_now >= $150`, `roi >= 15%`,
`roi <= 200%`, `confidence >= 15%`, priority only, not discovery, not
suspicious/nontradeable. The capture gate applies to **BIN freshness, not all
auctions** — a critical fix, because time-decayed auction capture previously
muted every useful early auction alert. An item is recorded in `alerts` only
after a delivery channel succeeds; failed Telegram sends retry next run. The
Telegram breaker is shared with the digest and trips after three failures.

**Digest** (`code/digest.py`): full scans only — top 25 profit opportunities and
top 25 live-auction grails, HTML links, chunked below Telegram's size limit. No
separate scheduler; it rides the full-scan schedule.

**Grails** (`code/grails.py`): 42 configured, ordered by personal significance,
mapped ~100 down to 40. Strict AND token matching with synonyms for
signed/autograph, first/1st, gameboy/game boy, pack/booster. Highest-score match
wins. Every discovered listing is tested. Default minimum substantial price
$3,000, optional per-grail max. Grails bypass profit output filters and max-row
trimming. **Grails are not synonymous with profitable flips.**

**Closer** (`code/closer.py`): after each live run, inspect recently ended
observed eBay auctions by item ID, read the actual winning bid plus known
shipping, record into `closed`, mark unsold as 0 to stop endless rechecks, leave
undetermined rows for retry, stop on challenge/breaker, limit 20 lookups per
run. Exact item-ID matching is the primary calibration source; legacy fuzzy
`match_closed()` remains a secondary path.

---

## 20. Database and learner

```text
database/history.db            20,803,584 bytes
comps                6,951     (frozen - the eBay sold lane has been cooling)
fair_history         6,438
observations        19,513
closed                 215
alerts                  44
guide_cache          4,575
guide_product_cache  3,377
source_health          164
```

```text
alerts(item_key PK, alerted_at)
closed(item_id PK, actual_price, closed_at)
comps(query, title, price, sold_date, url, site, scanned_at, comp_key, ...)
fair_history(query, ts, fair, n_comps, trusted)
guide_cache(query PK, value, ts)
guide_product_cache(query PK, payload, ts)
guide_meta(key PK, value)
observations(item_id, site, query, title, listing_type, price, shipping, bids,
             end_time, fair, predicted_settle, hours_left, observed_at,
             n_comps, confidence, trusted)
source_health(source, run_at, mode, status, ok, failed, skipped,
              freshness_hours, detail)
```

Migration code makes dated backups before material dedupe/quarantine. **Never
delete those backups without Andrew's explicit approval.** Do not mutate the
real database from ad hoc scripts — put migrations in `code/db.py`, back up,
test on a temp copy, then let the app run them.

**Learner state** (`model/learned_params.json`): `n: 0`, `n_snapshots: 0`,
filters `min_fair 50 / min_comps 3 / ratio_band [0.1, 3.0]`, training filter
`joined 1090, dropped_fair_below_floor 891, dropped_no_evidence_recorded 199`,
`ml.deployed: false`, `benched_why: "cold start"`.

This is deliberate. Historical closes were valued before the evidence and
valuation repairs. **Do not relax filters to make a learned number appear.**

Tiers: Tier 0 uses config `auction_settle_ratio=0.92`. Tier 1 needs ≥20
trustworthy distinct closed auctions and uses median actual/fair plus bands.
Tier 2 needs ≥150 distinct auctions, gradient boosting, GroupKFold by item, and
deploys only if it beats parametric by ≥3% with absolute CV MAE ≤0.25.
`model.pkl` may sit on disk while `ml.deployed` is false — the engine obeys the
deployment flag, not file existence.

Last close settlement: 0 settled, 0 unsold, 20 pending retry, 0/20 trustworthy.

---

## 21. Configuration snapshot (no credentials)

```text
sites: ebay, yahoo_jp, goldin, heritage, pristine
watchlist: 90 entries        grails: 42
marketplaces: EBAY_US, EBAY_GB, EBAY_DE
database.file: database/history.db     comp_cache_hours: 48

algorithm:
  auction_settle_ratio 0.92     default_resale_channel auto
  sales_tax_rate 8%             min_title_match 0.55
  outlier_mad_k 3               comp_half_life_days 30
  late_auction_hours 6          cost_model_tau_hours 24
  proxy_bid_per_bid 2%          proxy_bid_cap 15%
  sniper_floor 75%              capture_half_life_hours 12
  ungraded_grade PSA 5          guide_skip_min_comps 8
  min_specific_comps 3          mixed_pool_confidence_cap 25%
  subject_tokens 2

scraping:
  request_delay_seconds 3.5     ebay_sold_request_delay_seconds 10
  parallel_queries 10           html_timeout_seconds 12
  challenge_backoff_after 3     site_delays {130point 1.5, pristine 2.5}
  max_results_per_query 40
  max_results_per_query_by_site      {ebay: 500}
  max_results_per_query_by_site_bin  {ebay: 100}      <- NEW today
  targeted_comp_queries_per_run 20   / per_bin_run 6  / max_results 60
  close_lookups_per_run 20      international_priority_only true
  comps_warm_per_sweep 6        use_html_comps true    use_130point false
  circuit_breaker_failures 3    cooldown 30m -> max 24h

marketplace_costs:
  goldin    min buyer fee $19, ship $6 (<$1k) / $19 (>=$1k), insurance 0.9%
  heritage  buyer 22%, min $29
  pristine  buyer 17%, shipping_estimate $15                <- CHANGED today

filters:
  min_value $1,000  (Video Games $250)   max_price $100,000
  max_roi 200%      pokemon_grade_floor PSA 5
  flag_seller_feedback_below 10          too_good_ratio 35%
  min_listing_match 0.60                 pokemon_eras_only true
  video_games_sealed_or_graded true
  exclude_keywords: reprint, proxy, replica, custom card, digital, orica,
    fan made, fanmade, metal card, gold card, sticker decal, jumbo oversized,
    coaster, you pick, pick your, choose your, - pick, repack, poster,
    art print, chase box, set-break, set break

output: max_rows 1000, min_expected_value $0
  today: hours 24, fresh_hours 24, min EV $75, min confidence 25%,
         max_bid_target_roi 15%

japan: FX 0.0063, proxy $10, domestic ship $8, intl ship $35,
       insurance 1%, FX spread 3%, import duty planning 15%

alerts: enabled, priority only, min edge $150, ROI 15%-200%,
        confidence floor 15%, macOS notifications + sound, Telegram configured

api_keys configured: ebay, pricecharting, fanatics, alt
api_keys empty:      pokemontcg (so the source is now correctly silent)
```

---

## 22. Historical bugs that must not return

Use as a regression-review checklist.

1. Function-local `_category` import shadowed the global and killed every run.
   Keep `output_ok` module-level.
2. Zero-bid hybrid fake bargain — opening bid treated as takeable. Use known BIN.
3. Max Bid equaled Breakeven — preserve the target-ROI margin.
4. Vault fee stacking — vault 7% replaces the ordinary fee.
5. Tax/exit mismatch — vault tax exemption requires a vault exit.
6. Raw card under a graded query — ungraded must be valued as PSA 5, not inherit
   the query grade.
7. CGC guide inflation — price guides must route through effective grade.
8. Impossible grades — `CGC 85` must never become PSA 84.
9. Seller adjectives as subject — preserve subject-candidate filtering.
10. Accent corruption — preserve Japanese-aware folding.
11. Card-number mixing — an exact numbered query rejects unnumbered comps.
12. Wrong-year mixing — old cards cannot use modern tribute comps.
13. Excluded keywords only on listings — the blacklist must also screen comps.
14. Duplicate comp URLs — canonical item identity must collapse URL variants.
15. Weak evidence poisoning the learner — preserve the central evidence gate.
16. Legacy fair history driving marks — only `trusted=1`.
17. Discovery/ask-based/mixed pool entering alerts — preserve the gate.
18. Crossover games — WATA/VGA must not enter the card Crossover sheet.
19. Sealed Pokemon classified as games — platform keywords before Pokemon, title
    keywords after; "sealed" is not itself a game category.
20. Challenge retry spam — preserve the run-wide tally and persistent cooldown.
21. 429 automatic retries — never retry a 429 immediately.
22. NULL guide cache poisoning — a network failure must not cache a miss.
23. Empty report padding — never resurrect rejected raw rows.
24. Model leakage — GroupKFold by auction item, not random snapshots.
25. Snapshot-weighted settle median — learner Tier 1 is per auction.
26. Stale learned model after cold start — always write parameters and obey the
    deployment state.
27. `UnboundLocalError: 'key'` in the relevance loop — `_listing_identity()` must
    be assigned on every path before `relevant.append((key, listing))`.
28. **Stray relative-path databases** — every DB connection resolves through
    `paths`, never a bare filename. (§1.1)
29. **Source Health lying about a source** — advertised status and runtime
    behavior must be bound by a test. (§1.2)
30. **A sweep that outlives its own schedule** — per-mode result ceilings. (§1.3)

---

## 23. Conventions

- Edit the real Desktop project, not a scratch copy.
- Preserve unrelated user changes; use targeted patches.
- Read `config.yaml` fresh each session; never echo secrets.
- Add a regression test for every material bug, and **verify the test fails
  against the pre-fix code**.
- Prefer exact examples from Andrew's own reports.
- Run the unit suite and the demo before declaring anything done.
- Use live network smoke tests only when necessary and respectful.
- Respect persistent breakers; never delete breaker state to force a request.
- A code change is not done until the one-file-per-run Excel output reflects it.
- Update this handoff when architecture, source status, report columns, test
  count, or Git state changes.

---

## 24. Fresh-instance startup checklist

```bash
cd "/Users/alekas/Desktop/ebay opportunities"
git status --short
git log --oneline --decorate -10
.venv/bin/python -m unittest discover -s code -p "test_*.py"   # expect 156, OK
tail -120 logs/scan.log
cat state/.breaker_state.json
cat state/.scan.lock 2>/dev/null
ls -lt "reports/Opp Runs" "reports/BIN runs" | head -20
```

Then read, in order: this file, `code/main.py`, `code/scrapers/ebay.py`,
`code/valuation/price_guide.py`, `code/quality.py`, `code/report.py`,
`code/test_fixes.py`, and `config.yaml` (structure only — never expose
credentials).

Do not launch a full scan just to watch it fail while eBay's sold lane is in
cooldown. If Andrew has switched goals, understand the new direction first. If
returning to scanner quality, begin with exact identity and product-class gates
— not source expansion and not looser thresholds.

---

## 25. "Picked up where we left off" checklist

A new instance is correctly oriented when it can state all of the following:

- The scanner is a decision and evidence pipeline, not eBay scraping.
- HEAD is `4034523`; local `main` is 3 commits ahead of `origin/main`; the
  working tree holds a large intentional uncommitted set that must be preserved.
- 156 tests pass.
- eBay's **Browse API is healthy**; only the **sold-comp HTML lane** is cooling.
- The eBay API in-flight breaker race is fixed and covered by a test.
- Goldin and Pristine are live; Heritage is recovering; Yahoo/Buyee is broken at
  the parser; Fanatics, ALT, and 130point are deliberately disabled.
- The automatic exit optimizer is built and integrated everywhere; PSA Vault
  economics are exclusive; buyer premiums are part of landed cost; Max Bid and
  Breakeven are separate numbers.
- Legacy fair history and old closes are quarantined; the learner is
  intentionally cold at `n=0`.
- Today's four fixes: guide DB path, PokemonTCG gate, BIN result cap, Pristine
  shipping.
- The BIN cadence still needs to move to hourly on Andrew's Mac.
- **The next engineering task is exact product-class and parallel/reference
  identity in valuation** — not source expansion, not looser thresholds.

If any of those statements changes, update this document immediately.

---

## 26. 2026-07-26 evening update — global identity, Vault eligibility,
trade blotter, and source manifests

This section supersedes stale counts and “next task” statements above.
Current regression result is **298 tests passing**, not 156. The exact
product-class/parallel identity work described above has already been built;
the work in this section addressed the next review findings.

### 26.1 User direction

Andrew approved this exact scope:

1. Fix the same physical listing appearing more than once because it was
   returned under both an exact query and a broad query.
2. Fix watches/watch parts being assigned PSA Vault tax and exit economics.
3. Do **not** change the third review finding (Source Health/PriceCharting
   call-pressure semantics). Andrew wants to continue solving PriceCharting
   pressure by expanding local CSV coverage.
4. Build feature 2: a persistent trade blotter/review workflow.
5. Build feature 3: a standardized source-onboarding system.
6. Skip feature 1 (fresh sold-market evidence) for now.

No Git commit was made in this work. Preserve the entire dirty worktree.

### 26.2 Bug fix: global cross-query physical-listing dedupe

**Observed failure:** the same eBay Raichu #14 listing could appear twice in
Today/Action because it was found under both:

- `1999 1st Edition Base Set Raichu Holo PSA 8`
- `1999 1st Edition Pokemon Set`

The old `_dedupe_listings()` call only deduped the listings returned inside
one query. `prepared` then retained one copy per query, so both copies were
valued and reported.

**Implementation:**

- `Listing.matched_queries` now carries every query that surfaced one
  physical listing.
- `main._dedupe_prepared()` performs a second, global pass after all query
  relevance gates and before targeted comp planning.
- Identity remains conservative:
  - trusted canonical asset/certificate ID;
  - otherwise source + native listing ID;
  - otherwise canonicalized source URL;
  - title equality alone never collapses separate copies.
- The retained context is selected by:
  1. non-discovery before discovery;
  2. priority before non-priority;
  3. stronger title/query match;
  4. more specific query token count;
  5. lower landed cost as a tie breaker.
- The winning query is always first in `matched_queries`, even if the broad
  query ran first.
- Priority is preserved if any duplicate context was priority.
- A non-discovery context wins over discovery.
- The highest-significance grail tag is preserved.
- Ask pools are rebuilt from the deduped rows.
- The pass runs before targeted comp planning, so a broad duplicate cannot
  spend an additional paid/cache-miss exact-comp lookup.
- Valuation audit notes record the other queries that found the listing.
- The filter waterfall records `duplicate across queries`.

Regression:
`TestConnectorCapabilitiesAndDedupe.test_same_listing_across_queries_keeps_exact_context_once`
starts with the broad context first, proves only one row survives, proves the
exact query is retained, and proves both matched queries remain auditable.

### 26.3 Bug fix: strict PSA Vault asset eligibility

**Observed failure:** Watches rows received `PSA Vault`, 0% buy-side tax, and
the 7% Vault exit fee merely because the eBay price cleared `$500`.

The old engine gate tested only:

```text
vault enabled + eBay + price >= threshold
```

**Implementation:**

- `economics.psa_vault_eligible()` is now the shared asset gate.
- Default eligible categories are exactly:
  - Pokemon Cards
  - Sports Cards
- `valuation.identity.object_class(title)` must be `card`.
- Grading is required by default.
- Default eligible graders are PSA, BGS, CGC, SGC, and BVG.
- The listing must be on eBay.
- `ValuationEngine._vault_route()` uses the shared gate.
- `report._bid_levels()` uses the same gate, so workbook Max Bid/Breakeven
  cannot disagree with live valuation.
- `best_exit_route()` enforces the gate defensively, including a manual
  `psa_vault` override.
- Watches, watch parts, raw cards, games, comics, figures, wrappers, and
  memorabilia retain ordinary checkout tax and resale fees.
- Eligible graded Pokemon/sports-card Vault behavior remains unchanged.

Live ignored config now includes the explicit policy:

```yaml
algorithm:
  psa_vault:
    eligible_categories: [Pokemon Cards, Sports Cards]
    requires_graded: true
    eligible_graders: [PSA, BGS, CGC, SGC, BVG]
```

Regression coverage includes an eligible graded card, a watch, an ungraded
card, Vault-threshold branch validation, and the existing whole-dollar
boundary tests.

### 26.4 Feature: persistent CSV-backed Trade Blotter

Canonical private file:

```text
trade_blotter/trade_blotter.csv
```

It has been initialized with headers and is ignored by Git. It is the source
of truth; the workbook is a read-only snapshot.

Core module: `code/trade_blotter.py`

Each live scan:

1. loads/migrates the canonical CSV;
2. selects up to 50 strongest tradeable non-watch rows;
3. excludes discovery rows and, by default, grails;
4. upserts by trusted listing/asset identity;
5. refreshes market fields;
6. preserves user-editable workflow/cash-flow fields;
7. derives P&L fields;
8. atomically replaces the CSV.

Default statuses:

```text
Discovered
Verified
Watching
Bid/Offer Placed
Won
Lost
Received
Listed
Sold
Passed
```

Human-editable fields include status, verified, planned bid/offer, actual
purchase price, buyer fees, shipping, tax, won/received/listed/sold dates,
asking price, net sale proceeds, and notes.

Derived fields:

- actual landed cost
- realized profit
- realized ROI
- holding days

The CSV records:

- stable listing key;
- first/last seen;
- source/type/title/URL;
- best query and every matched query;
- valuation identity;
- current item price and shipping quote;
- fair value/confidence/edge/ROI/score;
- suggested max bid and breakeven;
- selected exit and expected net proceeds;
- user workflow and realized cash flows.

Safety/operations:

- writes are atomic;
- schema migrations make a timestamped backup;
- an unreadable existing blotter raises and leaves the file untouched rather
  than overwriting it;
- the private CSV and its backups are Git-ignored;
- `Open Trade Blotter.command` ensures the file exists and opens it;
- `trade_blotter/README.md` explains which fields to edit;
- demo mode never writes the persistent CSV.

Workbook:

- every scanner-produced workbook now receives `Trade Blotter` immediately
  after Action;
- it is a styled snapshot with frozen panes, filters, currency/percentage
  formats, status colors, hyperlinks, and realized-P&L coloring;
- About explicitly says to edit the CSV, not the workbook.

Ignored live config:

```yaml
trade_blotter:
  enabled: true
  file: trade_blotter/trade_blotter.csv
  auto_capture_top_n: 50
  include_grails: false
```

Tests prove initial capture, identity upsert, preservation of user edits,
derived realized P&L/ROI/holding days, and workbook-sheet creation.

### 26.5 Feature: manifest-driven source onboarding

New architecture:

- `code/source_registry.py`
- `code/scrapers/manifest_feed.py`
- `code/source_onboarding.py`
- `source_manifests/_example.yaml`
- `source_manifests/README.md`
- `imports/README.md`
- `Onboard Source.command`
- `Check Source Feeds.command`

An authorized source no longer needs a Python orchestration edit. A manifest
declares:

- stable source ID/display name;
- enabled/disabled state;
- auction/fixed capabilities;
- local JSON/CSV export or explicitly authorized endpoint;
- field mapping (dotted paths work for nested JSON);
- buyer premium, minimum fee, shipping, insurance, international shipping,
  duty, FX, and marketplace defaults.

Required normalized mappings are title, URL, and current price. Manifests may
also map ID, type, bid count, end time, image, status, grader, certificate,
query tags, or any normalized field the authorized-feed parser understands.

Runtime behavior:

- enabled manifests automatically join every scan; no duplicate `sites:`
  edit is required;
- advertised auction/fixed capabilities drive orchestration;
- `.json` and `.csv` local exports load once per run;
- remote endpoints receive `q`, `type`, and `limit`;
- no network call occurs unless both authorization and endpoint are present;
- manifest economics flow into the same landed-cost model;
- trusted certificate identity can dedupe the same slab across venues;
- each manifest receives a dynamic Source Health row;
- invalid manifests are logged and skipped without breaking other sources.

Credential policy:

- raw token/API-key/password/secret fields are rejected during validation;
- secrets belong in ignored `secrets.yaml` under
  `api_keys.<source_id>` or in environment variables named by
  `access_token_env` / `api_key_env`;
- manifests can safely remain tracked.

User workflow:

1. Copy `source_manifests/_example.yaml`.
2. Fill in source ID, access/feed, field map, and economics.
3. Drag the YAML onto `Onboard Source.command`.
4. Run `Check Source Feeds.command` for a no-network validation pass.
5. Put the authorized export in `imports/`, or configure an approved endpoint.
6. Set `enabled: true`.

Tests prove valid registration, automatic enablement, dynamic Source Health
matching, rejection of embedded secrets, CSV field mapping, fee/shipping
defaults, and no network call for a local feed.

### 26.6 PriceCharting/CSV decision

Per Andrew's instruction, Source Health semantics were **not** loosened or
reinterpreted here. PriceCharting request pressure continues to be addressed
by local guide CSV coverage:

- current local guide data: 289,833 rows across three files;
- CSV answers cost no API call;
- API remains a fallback for items not covered locally;
- the paid API request budget and pacing remain intact.

Do not “fix” this later by hiding genuine PriceCharting call/budget state.
Expand and refresh the local guide CSV corpus instead.

### 26.7 Verification completed

Pre-fix regression check intentionally produced:

- watch Vault assertion failure;
- missing global `_dedupe_prepared`;
- missing trade blotter;
- missing source registry.

After implementation:

```text
Ran 298 tests in ~1.6s
OK
```

Demo:

```text
15 raw -> 15 relevant -> 15 valued -> 7 kept
8 Research/Filtered explanations
no outward API calls
exit 0
```

The demo workbook was imported with the bundled spreadsheet artifact runtime.
Verification found:

- Today first;
- Action second;
- Trade Blotter third;
- eight expected demo sheets;
- no `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, or `#N/A`;
- all eight sheets rendered successfully;
- visual inspection passed for headers, row formatting, hyperlinks, filters,
  conditional colors, diagnostic tables, and About text.

`git diff --check` is clean.

### 26.8 Current Git/runtime state

- HEAD remains `4034523`.
- The working tree still contains the large intentional uncommitted feature
  set from earlier work plus this section's changes.
- Do not reset, restore, or broadly stage.
- downloaded `guide_csv/*.csv` catalogs are large and now ignored; keep them
  as local runtime data and do not force-add them.
- `trade_blotter/trade_blotter.csv` is initialized and ignored.
- `config.yaml` is ignored and contains the live Vault/blotter policy.
- `imports/` keeps only its README tracked; user/platform exports are ignored.
- Installed manifest backups are ignored.
- The three new `.command` files are executable.

Before a future commit, inspect exact targets and avoid `git add .`.

### 26.9 Fresh-instance priority after this work

The next useful step is **not** another architecture rewrite. Run one full live
scan with the current code, then inspect:

1. whether the old Raichu/Chansey cross-query duplicates are now one row;
2. whether every Watches row uses ordinary (non-Vault) economics;
3. how many rows auto-populate the Trade Blotter;
4. whether source-manifest rows appear correctly in Source Health once a real
   feed is installed;
5. which watchlist families still miss the local PriceCharting CSV corpus.

Only after that evidence should thresholds, auto-capture count, or source
economics be tuned.
