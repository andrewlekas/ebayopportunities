# Card Arbitrage Scanner — SOL Extra-High Context Handoff

Last reconciled against the repository, local configuration, SQLite database,
Git history, test suite, reports, state files, live scan log, and a fully
rendered inspection of the latest production workbook on
**2026-07-26 at approximately 13:05 America/Chicago**.

This document is intentionally exhaustive. It is meant to let a new Codex
window, another AI coding system, or a human engineer resume the project
without access to the conversation that produced it.

It supersedes stale statements in `HANDOFF.md`, `fable_handoff.md`,
`docs/README.md`, `docs/FEATURES.md`, and `NEXT_RUN_CHECKLIST.md`, while
preserving the historical reasoning behind their safeguards. Those files are
still useful history, but this file is the current operational handoff.

## 0. Read this first: current state in one page

The project is a local Mac-based collectibles arbitrage scanner for Andrew.
It searches live auctions and fixed-price listings, values them from sold
comps and price guides, estimates a realistic acquisition price, calculates
net resale proceeds, selects the best eligible exit channel, and writes a
single Excel workbook per run. It also tracks positions, sends Telegram
alerts/digests, learns from auction closes, and persists source health.

The real project root is:

```text
/Users/alekas/Desktop/ebay opportunities
```

Current core implementation commit:

```text
6492601 feat: restore auction sources and optimize exit routes
```

Current branch: `main`.

Tracked working tree was clean immediately after `6492601`, but it is **not
clean now**. A large, intentional, tested implementation set remains
uncommitted. Preserve it. The final addendum at the end of this document is
the authoritative description of the current working tree and latest live
run.

Remote:

```text
origin  https://github.com/andrewlekas/ebayopportunities
```

At the time of this handoff, local `main` was two implementation commits
ahead of `origin/main`:

```text
origin/main  0ce1780 fix: price exact cards and validate bid routes
local main   8fb14fc feat: complete economics history and source readiness
local main   6492601 feat: restore auction sources and optimize exit routes
```

The regression suite is green:

```text
Ran 114 tests
OK
```

The synthetic end-to-end demo also passes and writes a valid workbook.

Current active sites in the ignored local `config.yaml`:

```yaml
sites:
  - ebay
  - yahoo_jp
  - goldin
  - heritage
```

Current production source reality:

- **Goldin works live.** The repaired adapter returned real auction
  inventory in both isolated smoke tests and a completed production run at
  22:22. Source Health marked it healthy.
- **Heritage transport and parser work live, but the source is not fully
  stable.** An isolated smoke test returned three real current-bid listings.
  In the 22:22 full run it produced six successful request/parse pairs, then
  three transport failures and opened its breaker; Source Health marked it
  cooling.
- **eBay is currently rate-limited/challenged.** Browse API calls are
  returning HTTP 429 and the HTML lane is in a persistent bot-challenge
  cooldown.
- **The eBay API breaker still has an in-flight concurrency race.** A live
  22:22 run logged 30 API 429 failures after the nominal three-failure trip
  because many parallel API requests passed the breaker before the shared
  failure count changed. This is the first code issue to fix.
- **Yahoo/Buyee transport currently returns pages, but the old
  `li.itemCard` schema is absent.** Its new parser canary correctly records a
  failure rather than treating HTTP 200 as working inventory.
- **Fanatics Collect is disabled.** The old Algolia path is obsolete; the
  current public search hostname did not resolve during inspection, and
  anonymous GraphQL required authentication. Do not fake coverage.
- **130point is disabled.** It is Cloudflare-walled and was generating
  pointless traffic.
- **The learner is deliberately cold.** `model/learned_params.json` has
  `n: 0`, and ML is not deployed. This is correct because historical training
  evidence was contaminated by earlier valuation bugs.

Immediate priority order:

1. Make the eBay API breaker atomic/reservation-based so only three
   concurrent failures can reach the wire.
2. Repair or replace Yahoo/Buyee listing ingestion.
3. Let eBay cooldowns expire, then run a fresh full production acceptance
   scan and review the resulting Today sheet.
4. Push the two local implementation commits (and this handoff, if committed)
   to `origin/main` when Andrew wants the remote updated.
5. Add verified exit economics for Probstein and COMC; revisit Fanatics only
   when its anonymous inventory service is reachable.

## 1. User, goal, and working style

The user is Andrew. He is a former ETF trader deploying substantial working
capital into collectible inventory. His main target is profitable resale,
with a separate personal-collection grail strategy.

Primary categories:

- graded vintage Pokemon cards;
- vintage and premium sports cards;
- sealed or professionally graded vintage video games;
- selected high-end watches;
- selected comics/pop-culture material;
- personal-collection grails that matter regardless of short-term profit.

The desired product is not a generic scraper. It is a trading decision
system:

1. Search multiple marketplaces for auctions and fixed-price listings.
2. Build a defensible fair value from sold comps and price guides.
3. Model where auctions will actually close rather than pretending the
   displayed bid is the cost.
4. Include every material acquisition cost.
5. Select the best eligible resale venue by expected net proceeds.
6. Calculate expected profit, ROI, liquidity/capital velocity, Max Bid, and
   Breakeven.
7. Produce one concise Excel workbook per run, led by a Today decision list.
8. Send only high-quality Telegram alerts and a daily digest.
9. Learn from actual closes without contaminating the model with weak
   valuations.

Andrew is not a developer. His normal interface is:

- double-clicking `.command` files;
- opening the resulting `.xlsx` report;
- reading `logs/scan.log` when something is wrong;
- editing `config.yaml`;
- pasting suspicious report rows back into a coding session.

Communicate in trader language: edge, return, turnover, capital velocity,
mark-to-market, evidence quality, risk, and bid ceilings. Lead with the
outcome. If Andrew says a row looks nonsensical, assume it may expose a real
root-cause bug. Reproduce the exact row as a regression test before changing
general logic.

## 2. Non-negotiable safety and product rules

These rules exist because violating them previously produced materially wrong
buy decisions.

### 2.1 Credential hygiene

Never print, paste, commit, or repeat API keys, Telegram credentials, tokens,
or secret-bearing URLs.

`config.yaml` is ignored by Git and currently contains local credentials.
The code also supports the safer ignored `secrets.yaml` overlay and
environment variables. Do not convert this handoff into a credential dump.

Credential precedence:

1. values already present in `config.yaml`;
2. ignored `secrets.yaml` beside the config;
3. environment variables, which win.

Supported environment variables are defined in `code/main.py`:

```text
CARD_SCANNER_EBAY_CLIENT_ID
CARD_SCANNER_EBAY_CLIENT_SECRET
CARD_SCANNER_PRICECHARTING_TOKEN
CARD_SCANNER_POKEMONTCG_API_KEY
CARD_SCANNER_FANATICS_APP_ID
CARD_SCANNER_FANATICS_SEARCH_KEY
CARD_SCANNER_TELEGRAM_BOT_TOKEN
CARD_SCANNER_TELEGRAM_CHAT_ID
CARD_SCANNER_SECRETS_FILE
```

`secrets.example.yaml` is tracked and safe to copy.

### 2.2 Test before live network work

Every live scan runs the full regression suite first. The gate fails closed.
Exit code 2 means tests failed and no live work should happen.

After a code change:

```bash
cd "/Users/alekas/Desktop/ebay opportunities"
.venv/bin/python -m unittest discover -s code -p "test_*.py"
.venv/bin/python -B code/main.py --demo -o /tmp/scanner-demo.xlsx
```

The double-click equivalent is `Run Tests.command`.

### 2.3 Do not weaken evidence gates to make the workbook fuller

An empty or thin workbook is safer than confident nonsense. Exact-card and
grade guards intentionally make some broad searches go quiet. The remedy is
better targeted queries or better comps, not loosening matching rules.

### 2.4 A transport success is not parser success

HTTP 200 with a changed schema is a failed source. Goldin, Heritage,
Fanatics, and Yahoo/Buyee now write explicit parser outcomes into the shared
API statistics. Preserve this distinction.

### 2.5 Never mix incompatible economics

- PSA Vault's 0% acquisition-tax treatment is tied to keeping the item in
  the vault. Do not combine the tax benefit with an ordinary non-vault exit.
- A vault fee replaces the ordinary marketplace fee; it is not stacked.
- A buyer premium with a minimum is nonlinear and must use
  `Listing.buyer_fee()` plus the correct inverse.
- Max Bid is a target-return ceiling. Breakeven is a zero-profit wall. They
  are not the same number.
- A zero-bid hybrid auction must be priced at its known Buy It Now, not the
  opening bid.

### 2.6 Browsing rows and actionable rows are different products

Questionable values can remain visible in category/research sheets, but they
must not enter Today, Action, alerts, digest, fair-history trends, portfolio
marks, or learner training unless they pass the appropriate centralized
gate.

## 3. Repository and filesystem map

Current project layout:

```text
ebay opportunities/
  Run Scan.command
  Run BIN Sweep.command
  Run Tests.command
  Setup Telegram.command
  Test Alerts.command
  Test Comps.command
  Fix Mac Sleep.command
  HANDOFF.md
  fable_handoff.md
  sol extra high handoff.md
  NEXT_RUN_CHECKLIST.md
  config.yaml                 # ignored; local settings and currently secrets
  secrets.yaml                # optional and ignored
  secrets.example.yaml        # tracked template
  .gitignore
  .venv/

  code/
    main.py
    models.py
    economics.py
    quality.py
    report.py
    db.py
    learner.py
    closer.py
    alerts.py
    digest.py
    grails.py
    portfolio.py
    misspell.py
    textutil.py
    security.py
    source_health.py
    paths.py
    demo_data.py
    test_fixes.py
    scrapers/
      base.py
      ebay.py
      goldin.py
      heritage.py
      yahoo_jp.py
      fanatics_collect.py
      point130.py
      __init__.py
    valuation/
      engine.py
      comps.py
      price_guide.py
      __init__.py

  database/
    history.db
    history.db-pre-...         # migration safety backups when present

  model/
    learned_params.json
    model.pkl

  portfolio/
    portfolio.csv

  reports/
    Opp Runs/
    BIN runs/

  logs/
    scan.log
    scan.log.1
    scan.log.2
    scan.log.3

  state/
    .scan.lock
    .breaker_state.json
    .cookies_<site>.json

  setup/
    requirements.txt

  test results/
    test_results.log
    comps_test_result.txt

  docs/
    README.md
    FEATURES.md
```

`code/paths.py` is the source of truth for folder names. Paths resolve
against the folder containing `config.yaml`, not the shell's current
directory. This fixed a previous bug where launching from a different
directory silently created or read the wrong database.

Do not move the project into an iCloud-evicted Desktop location. The folder
once disappeared during a macOS/iCloud Desktop migration and had to be
restored from “Desktop - Andrew's MacBook Air (2).”

## 4. Current environment and dependencies

Observed environment:

```text
macOS 26.5.2 (build 25F84)
Python 3.9.6
```

Important installed versions:

```text
joblib         1.5.3
scikit-learn   1.6.1
curl_cffi      0.13.0
openpyxl       3.1.5
PyYAML         6.0.3
beautifulsoup4 4.15.0
requests       2.32.5
```

`Run Scan.command` installs/synchronizes `setup/requirements.txt`. The BIN
sweep assumes the environment already exists; run a full scan once after
dependency changes.

`curl_cffi` is important. It gives requests a real browser-like TLS/HTTP2
fingerprint. Plain `requests` was blocked by eBay/130point even with browser
headers.

## 5. Git state and today's commit chain

Current history:

```text
6492601 feat: restore auction sources and optimize exit routes
8fb14fc feat: complete economics history and source readiness
0ce1780 fix: price exact cards and validate bid routes
311f08f fix: gate learning and actions on trusted evidence
78db6b0 fix: deduplicate comps by canonical listing identity
54ff23f security: redact credentials and support external secrets
a104a55 chore: establish card scanner baseline
```

Tags on the baseline include `v0.1.0` and `baseline-2026-07-25`.

What each post-baseline commit did:

### `54ff23f` — secrets and log hygiene

- Added `code/security.py`.
- Redacts credential-looking query/path/header text from failures.
- Added `secrets.yaml` and environment override support.
- Updated network, alert, digest, and guide logging.
- Added credential-hygiene regression tests.
- Kept backward compatibility with local inline secrets.

### `78db6b0` — comp identity and migration

- Added canonical eBay item identity independent of URL tracking variants.
- Prevents one sold listing from counting multiple times because URL or
  price text changed.
- Added a safe migration, backup, canonical comp key, and dedupe tests.

### `311f08f` — unified trust policy

- Added `code/quality.py`.
- Centralized hard-risk blockers for Action/Today/alerts/digest.
- Centralized evidence rejection for fair history and learner observations.
- Prevented weak, disputed, ask-based, mixed-pool, discovery, regraded,
  thin-comp, or collection-rejected evidence from contaminating learning.
- Preserved category tabs as research views.

### `0ce1780` — exact-card pricing and bid-route validation

- Added cache-first, rate-capped targeted comp searches using card number and
  listing grade.
- Broad searches still discover inventory, but exact items need an exact
  pool before becoming tradeable.
- Corrected Max Bid/Breakeven route validation around the $500 vault
  boundary.

### `8fb14fc` — original plan items 7–10

- Restricted Crossover to configured card graders/categories.
- Added one complete landed-cost equation for Japan/international buys.
- Added configurable exit channels.
- Quarantined legacy fair history with `trusted=NULL`.
- Added persistent Source Health snapshots and workbook sheet.
- Test count reached 105.

### `6492601` — marketplace recovery and automatic exits

- Repaired Goldin using the current `lots_v2` service.
- Repaired Heritage current search transport and parser.
- Added buyer-premium rates and minimums to acquisition economics.
- Added parser canaries.
- Built automatic exit-channel optimization.
- Applied the exit route to engine EV/Edge, bid levels, workbook, and
  portfolio marks.
- Updated both older handoffs.
- Test count reached 114.

Do not rewrite these commits unless Andrew explicitly asks. Normal next Git
steps are:

```bash
cd "/Users/alekas/Desktop/ebay opportunities"
git status
git log --oneline --decorate -10
git push origin main
```

Do not push without Andrew's direction if there is any doubt about the remote
workflow.

## 6. How to run it

### 6.1 Andrew's normal full scan

Double-click:

```text
Run Scan.command
```

It:

1. `cd`s to the project root;
2. creates `.venv` if needed;
3. synchronizes `setup/requirements.txt`;
4. creates `reports/Opp Runs`;
5. launches under `caffeinate -im`;
6. writes a timestamped workbook;
7. opens it when launched from an interactive terminal.

Equivalent shell command:

```bash
cd "/Users/alekas/Desktop/ebay opportunities"
caffeinate -im .venv/bin/python -B code/main.py \
  -c config.yaml \
  -o "reports/Opp Runs/opportunities_$(date +%Y-%m-%d_%H.%M).xlsx"
```

### 6.2 BIN sweep

Double-click:

```text
Run BIN Sweep.command
```

Equivalent:

```bash
caffeinate -im .venv/bin/python -B code/main.py \
  --mode bin \
  -c config.yaml \
  -o "reports/BIN runs/bin_sweep_$(date +%Y-%m-%d_%H.%M).xlsx"
```

The command retains only the 30 newest BIN workbooks.

`bin.priority_only` is currently `false`, so BIN sweeps cover all watchlist
queries. The source-health/network breaker code is shared with full scans;
there is not a separate scanner implementation for BIN mode.

### 6.3 Tests

Double-click `Run Tests.command`, or:

```bash
.venv/bin/python -m unittest discover -s code -p "test_*.py" -v
.venv/bin/python -B code/main.py --demo -o /tmp/scanner_selfcheck.xlsx
```

`Run Tests.command` also checks that required local credentials are present
without printing them and records output in:

```text
test results/test_results.log
```

### 6.4 Calibration

```bash
.venv/bin/python -B code/main.py --calibrate
```

### 6.5 CLI options

```text
--mode all|auctions|bin
--demo
--calibrate
--skip-self-test
-c / --config
-o / --output
-v / --verbose
```

`--skip-self-test` is an emergency/diagnostic escape hatch, not a normal
operating mode.

### 6.6 Exit codes

- `0`: successful report, successful demo/calibration, or a BIN sweep that
  deliberately skipped because another scan owns the lock.
- `1`: no actionable/live inventory or runtime/source failure.
- `2`: regression self-test failed before scanning.

An exit 1 with no workbook is not necessarily “no bargains.” Read
`logs/scan.log` and Source Health to determine whether inventory sources
failed.

## 7. End-to-end runtime architecture

### 7.1 Boot and configuration

`code/main.py`:

1. parses CLI arguments;
2. resolves all paths from the config location;
3. loads ignored secrets/environment overlays;
4. initializes rotating logging;
5. runs the 114-test subprocess unless demo/calibration/skip;
6. obtains the process lock for live work.

The self-test is intentionally a subprocess so mocked modules, temporary
databases, and patched logging cannot leak into the live scan process.

### 7.2 Run lock behavior

The lock lives at:

```text
state/.scan.lock
```

It contains:

```text
<pid> <mode> <started>
```

Behavior:

- BIN sweep sees a holder: logs the holder and exits 0.
- Full/manual scan sees a holder: sends SIGTERM, waits up to 20 seconds,
  then SIGKILL if needed, and takes over.
- Demo/calibration bypass the lock.

This was chosen by Andrew because frequent slow sweeps previously made
manual scans almost impossible to start.

### 7.3 `run_live` phases

The pipeline deliberately separates network work from SQLite writes.

#### Phase A — main-thread cache planning

- Open `database/history.db`.
- Instantiate configured scrapers.
- Read cached comp pools for each watchlist query.
- Add grail discovery queries on a full scan.
- Prefetch one eBay OAuth token.

#### Phase B — parallel network fetch

`ThreadPoolExecutor` currently uses 10 workers.

For each query:

- fetch comps if cache is absent: 130point when enabled, then eBay sold HTML;
- search auctions on every configured site;
- search eBay fixed-price inventory for `all` and `bin`;
- search international/Japan sources for priority queries unless configured
  otherwise;
- run typo variants for priority queries on full scans.

HTML requests remain serialized per scraper instance by a lock. API requests
are concurrent.

One query failure is caught and logged; it does not abort the unattended run.

#### Phase C — main-thread persistence and relevance

- Save newly fetched comps.
- Fall back to stale comps when live refresh is blocked.
- Remove comps containing excluded keywords.
- Dedupe live listings by canonical item ID/URL.
- Apply grade, language, variant, subject, title-match, cap, and max-price
  guards.
- Set priority/discovery/category.
- Set `resale_channel` from watchlist or default `auto`.
- Tag grails.
- Build live fixed-price ask pools.

#### Phase D — exact-card targeted comps

Broad queries discover cards but cannot safely price every card number.

- `plan_targeted_comp_queries` deduplicates and prioritizes exact queries.
- Full scans currently allow 20 targeted queries.
- BIN scans allow 6.
- Each query can fetch 60 results.
- At most four targeted workers are used.
- Cached exact pools are preferred.
- Thin/missing exact pools trigger a quarantined mixed-pool fallback.

#### Phase E — valuation and trusted persistence

For each relevant listing:

- call `ValuationEngine.evaluate`;
- select specific versus broad comp pool;
- score acquisition and exit economics;
- persist only evidence that passes `quality.evidence_rejection`;
- record a query-level trusted fair once per run;
- record observations for later close matching.

BIN sweeps then warm up to six background comp caches if the source is still
healthy.

### 7.4 After collection

`main()`:

1. captures and persists Source Health;
2. applies per-category minimum fair-value floors;
3. removes impossible ROI rows;
4. applies collecting standards;
5. applies output/actionability rules;
6. trims to `max_rows`, never trimming grails;
7. builds portfolio marks;
8. writes the workbook;
9. sends alerts;
10. sends full-scan digest;
11. settles recently ended auctions;
12. retrains or reports cold learner state;
13. logs the top row;
14. always writes API summary and duration footer.

Unhandled exceptions are written to `logs/scan.log`; they no longer vanish
with a closed Terminal window.

## 8. Data model

### 8.1 `Listing`

Defined in `code/models.py`.

Core fields:

```text
site, title, url, current_price, shipping, bid_count, end_time,
image_url, listing_id, query, priority, discovery, misspell_from,
listing_type, best_offer, has_buy_now, buy_now_price, grail,
grail_score, created_at, currency, marketplace, seller_feedback
```

Exit/category fields:

```text
resale_channel   # default "auto"
category
```

Acquisition-cost fields:

```text
buyer_fees                 # fixed proxy/buyer fee
buyer_fee_rate             # percentage premium
minimum_buyer_fee          # nonlinear minimum premium
international_shipping
insurance_rate
import_duty_rate
fx_spread_rate
```

Important properties/methods:

```text
age_hours
hours_remaining
fixed_acquisition_cost
variable_acquisition_rate
buyer_fee(item_price)
landed_cost(item_price)
item_price_for_landed_cost(landed_cost)
landed_cost_note(item_price)
total_cost_now
```

Buyer premium:

```text
buyer_fee(P) = max(P * buyer_fee_rate, minimum_buyer_fee)
```

Landed cost:

```text
L(P) =
  P * (1 + insurance_rate + import_duty_rate + fx_spread_rate)
  + shipping
  + buyer_fees
  + international_shipping
  + buyer_fee(P)
```

The inverse correctly handles the percentage branch and the fixed-minimum
branch. Do not simplify it to one linear rate.

### 8.2 `SoldComp`

```text
title, price, sold_date, url, site
```

Price is intended to include shipping when known.

### 8.3 `Valuation`

Core:

```text
fair_value, comps_value, guide_value, n_comps, dispersion, confidence,
expected_cost, expected_value, edge_now, capture, trend_30d, roi,
sales_per_month, annualized_roi, opportunity_score
```

Exit results:

```text
resale_channel
resale_fee_rate
net_proceeds
exit_advantage
```

Trust/diagnostic state:

```text
regraded
disputed
notes
audit_notes
```

`notes` are human decision notes. `audit_notes` are model diagnostics hidden
in Excel column AD.

### 8.4 `Opportunity`

An `Opportunity` pairs one `Listing` with one `Valuation`. Report dedupe may
attach a dynamic `dupe_note`.

## 9. Marketplace and valuation source adapters

### 9.1 eBay

File: `code/scrapers/ebay.py`.

Listings:

- official Browse API;
- auction and fixed-price;
- US/GB/DE marketplaces;
- FX conversion;
- seller feedback;
- created/end time;
- hybrid auction + fixed price;
- HTML fallback when necessary.

Sold comps:

- eBay sold-listing HTML;
- Chrome/TLS impersonation;
- challenge-page detection;
- cached fallback.

Current production problem:

- Browse API returns HTTP 429.
- HTML sold pages trigger bot challenges.
- Breakers persist cooldowns correctly.
- API concurrency still leaks requests already launched before the third
  failure. At 22:22 the log reached `(30/3)` before the work queue settled.

Required fix design:

- Add an atomic shared “attempt slots/reservations” mechanism per API lane,
  or serialize the decision to begin an API call.
- A simple check of `lane_tripped()` before request is insufficient when 10
  workers enter simultaneously.
- Preserve concurrency during healthy operation if possible.
- Add a regression test that releases many API workers simultaneously and
  proves no more than three failing calls hit the stub transport.
- Do not solve this by raising the failure threshold.

### 9.2 Goldin

File: `code/scrapers/goldin.py`.

Old dead endpoint:

```text
https://app.goldin.co/api/search/items
```

Current endpoint:

```text
POST https://d1wu47wucybvr3.cloudfront.net/api/lots_v2
Origin: https://goldin.co
Content-Type: application/json
```

Payload:

```json
{
  "search": {
    "queryType": "Search",
    "keyword": "<query>",
    "size": 50,
    "from": 0
  }
}
```

Expected schema:

```text
searchalgolia.lots[]
```

Fields used:

```text
status
title
current_price
min_bid_price
number_of_bids
end_timestamp
meta_slug
lot_id
buyer_premium
```

URLs:

```text
https://goldin.co/item/<meta_slug>
```

Buyer premium:

- read from every lot, observed at 22%;
- local configurable minimum: $19.

Exit economics:

- current local Goldin Marketplace seller fee assumption: 8.3%;
- auto only for eligible professionally graded Pokemon, sports, or games;
- minimum modeled resale value: $100.

Official references inspected:

```text
https://goldin.co/about.html
https://goldin.co/useragreement
```

The 8.3% rate is promotional/current and can change. Reverify periodically.

Live evidence:

- isolated smoke: three Michael Jordan PSA 9 lots;
- production 22:22 run: Goldin returned real counts such as 5 Venusaur,
  1 Movie Promo Charizard, 3 Chansey, 8 Blastoise, and 4 Charizard results
  before the log snapshot.

### 9.3 Heritage

File: `code/scrapers/heritage.py`.

Current search host:

```text
https://sports.ha.com/c/search/results.zx
```

Important query parameters:

```text
N=0
Nty=1
Ntt=<query>
dept=3923
mode=live
layout=list
```

The parser avoids brittle CSS classes. It:

- finds `/itm/` anchors;
- walks parent nodes until a `Current Bid:` block appears;
- extracts the current dollar bid;
- deduplicates hrefs;
- records a parser canary.

Buyer economics:

```text
buyer_fee_rate: 22%
minimum_buyer_fee: $29
```

These are configurable under `marketplace_costs.heritage`.

Official example used for fee verification:

```text
https://sports.ha.com/itm/baseball-cards/singles-1950-1959-/1955-topps-sandy-koufax-123-sgc-gd-2/a/152628-42077.s
```

Live isolated smoke returned three real current-bid listings. Current narrow
Pokemon queries often return zero, which is legitimate, not necessarily a
parser failure.

Heritage exit seller terms are not simple/public enough to trust
automatically. It is present as a manual channel at a provisional 20% fee
but `auto_enabled: false`.

### 9.4 Yahoo Japan / Buyee

File: `code/scrapers/yahoo_jp.py`.

Intended transport:

```text
https://buyee.jp/item/search/query/<translated-query>?translationType=98
```

Old expected schema:

```text
li.itemCard
.itemCard__itemName
a[href*="/jdirectitems/auction/"]
a[href*="/paypayfleamarket/item/"]
.g-priceDetails__item .g-price
```

The translation map covers major Pokemon names and Japanese vintage terms.
Watchlist entries can provide `query_ja`.

Current problem:

- requests return HTML successfully in some runs;
- no `li.itemCard` nodes appear;
- parser canary records `yahoo_jp/parse: failed`;
- every priority query currently returns zero listings.

Do not simply change the selector based on one guess. Inspect current browser
network traffic and response payloads. Determine whether:

- inventory moved to an API/XHR endpoint;
- HTML requires a session/cookie/region;
- markup changed;
- the page is an empty SPA shell;
- Buyee is selectively blocking automation.

The landed-cost model already supports Japan:

```text
$10 fixed proxy fee
$8 domestic shipping
$35 international shipping
1% insurance
3% FX spread
15% import-duty planning assumption
```

These are conservative configurable estimates, not customs advice.

### 9.5 Fanatics Collect

File: `code/scrapers/fanatics_collect.py`.

The retained code is explicitly a legacy Algolia adapter with a response
schema canary. It is not in `sites`.

Inspection found:

- current app bundle references a public universal-search hostname;
- that production hostname had no DNS during inspection;
- a development search host was behind Cloudflare Access;
- `https://app.fanaticscollect.com/graphql` exists, but anonymous listing
  access was not available;
- account-only GraphQL rejected anonymous requests;
- the browser marketplace shell loaded filters/footer but no inventory.

Do not paste old public search keys into the code or log them. Recheck the
first-party app only when its inventory service is functioning.

### 9.6 130point

File: `code/scrapers/point130.py`.

The rewritten path is:

```text
GET https://130point.com/api/search/html?q=<query>&saleType=sold
```

It parses `a[data-sold-result]`, including hidden accepted Best Offer prices
and real sale dates. However, Cloudflare currently blocks the endpoint and
even the homepage. `scraping.use_130point` is `false`.

Its unique value is real sold dates for velocity. Revival options are a
legitimate supported API, a durable browser-authenticated session, or
formally accepting eBay+PriceCharting as the comp stack. Do not hammer it.

### 9.7 Price guides

File: `code/valuation/price_guide.py`.

Sources:

- PriceCharting: configured and paid;
- PokemonTCG.io: optional and currently has no configured key.

Key behavior:

- guide cache versioning invalidates stale valuation-rule results;
- PriceCharting is skipped when there are at least eight strong comps;
- 429 `Retry-After` is respected;
- failures never cache `NULL`;
- every guide endpoint backs off after three consecutive failures;
- grades are priced at PSA-equivalent grade, not raw slab label;
- half grades interpolate without rounding upward;
- missing lower-grade rungs return no quote rather than inflating.

## 10. Network layer and breaker behavior

File: `code/scrapers/base.py`.

Transport:

- prefers `curl_cffi.requests.Session(impersonate="chrome")`;
- falls back to `requests.Session`;
- fallback retries selected 5xx errors but never 429;
- HTML timeout is 12 seconds;
- API timeout is 30 seconds;
- HTML lanes are serialized;
- API lanes are concurrent.

Per-run statistics:

```text
API_STATS[(endpoint, outcome)]
outcome = ok | failed | skipped
```

Run footer prints counts by endpoint.

Breaker state:

```text
state/.breaker_state.json
```

Per source/lane:

```text
<site>/api
<site>/html
```

Current configuration:

```text
trip after 3 consecutive failures
hard challenge backoff after 3 challenge pages
base cross-run cooldown 30 minutes
exponential escalation
maximum cooldown 24 hours
strikes reset after a quiet 24 hours
```

Persistent cookies:

```text
state/.cookies_<site>.json
```

Challenge behavior:

- every challenge counts even if a cooldown retry clears it;
- threshold wipes the cookie jar and persists a trip;
- new runs refuse to contact a lane until cooldown expires;
- one API lane can cool while another HTML lane remains healthy, and vice
  versa.

Known remaining race:

- HTML queued calls recheck the breaker inside the serialization lock.
- API calls do not have an equivalent atomic start reservation.
- Many API workers can therefore begin before the third failure is visible.

## 11. Comparable-sales hygiene

File: `code/valuation/comps.py`.

Base estimate:

- title-match floor, normally 0.55;
- MAD outlier trim, normally `k=3`;
- recency-weighted median;
- 30-day comp half-life.

A comp can be rejected for:

- effective grade mismatch;
- language mismatch;
- holo/non-holo/reverse mismatch;
- 1st Edition/unlimited/shadowless mismatch;
- Topsun original versus Topsun VS;
- sealed/CIB/loose mismatch;
- year mismatch;
- card-number mismatch;
- wrong subject;
- excluded keywords;
- too-low fuzzy title match.

### 11.1 Grade normalization

- PSA stays PSA.
- CGC/BGS/SGC/BVG use Andrew's one-full-grade-down rule against PSA.
- Effective grades floor at 1.
- WATA and VGA are video-game graders and do not receive the PSA card shift.
- Legacy SGC 100-point labels are normalized.
- Impossible grade tokens such as `CGC 85` are rejected and treated
  conservatively as ungraded.
- Ungraded is assumed PSA 5 for matching/valuation.

This assumption is a planning convention, not a claim that every raw card
will grade PSA 5.

### 11.2 Subject extraction

Set-wide queries previously injected seller adjectives like “investment
beautiful centering” as the subject. `subject_candidates()` now:

- removes set/grade/context/marketing vocabulary;
- uses real early title tokens;
- defaults to two subject tokens;
- handles accented Latin characters;
- preserves Japanese combining marks correctly.

Do not replace `textutil.fold()` with naive NFKD stripping; it once corrupted
Japanese dakuten.

### 11.3 Exact card number

Once a listing exposes a card number absent from the broad query, the engine
creates a specific query containing card number and grade.

`number_conflict` is deliberately asymmetric: if a query names a number,
unnumbered comps are also rejected. Allowing them reintroduced mixed medians.

If fewer than three exact comps are available:

- use quarantined broad fallback;
- add MIXED POOL note;
- cap confidence at 0.25;
- bar the row from action/alerts/history/learning.

Do not weaken this to make Jordan/Ruth searches look busier. Add explicit
card-number watchlist queries instead.

## 12. Fair value and scoring

File: `code/valuation/engine.py`.

### 12.1 Fair value

Order:

1. sold comps plus guide blend;
2. sold comps only;
3. guide only with 5% haircut;
4. ask-based estimate when neither exists;
5. no value.

Comps weight:

- grows with sample size;
- saturates near eight comps;
- shrinks with dispersion;
- has a 35% minimum when both comps and guide exist.

Ask-based:

- requires at least three live fixed-price asks;
- MAD trims;
- takes the lower quartile;
- applies a 10% haircut;
- confidence is capped around 0.35;
- is explicitly not tradeable.

If comps and guide differ by more than roughly 4x:

- `disputed=True`;
- confidence capped at 0.30;
- VALUE DISPUTED note;
- excluded from decision/evidence outputs.

### 12.2 Auction expected close

Base settle anchor:

```text
resale_value * auction_settle_ratio
```

Current cold-start ratio:

```text
0.92
```

Bid-aware adjustments:

- displayed bid gets a 2% proxy lift per bid;
- proxy lift capped at 15%;
- expected close interpolates toward settle anchor using a 24-hour time
  constant;
- in the last six hours, close is floored at 75% of the settle anchor;
- expected close never falls below the current bid;
- hybrid expected close never exceeds known Buy It Now;
- learned band/global ratio can override 0.92 when trustworthy data exists;
- deployed ML can override the parametric close ratio.

### 12.3 BIN behavior

- expected item price is the ask;
- fresh listing capture is high;
- capture decays with a 24-hour half-life;
- unknown age gets conservative capture;
- Best Offer note suggests roughly 80% of resale value, capped by ask.

### 12.4 Capture

Auction capture decays with time remaining using a 12-hour half-life, with a
floor. BIN capture decays with age. Too-good-to-be-true price and low seller
feedback reduce capture.

### 12.5 Confidence

Confidence combines:

- sample size;
- comp tightness;
- title-match quality;
- comps/guide agreement.

Listing/query match then scales it. Japanese and typo-discovery listings get
special handling but remain explicitly flagged for verification.

### 12.6 Score

```text
opportunity_score =
  min(roi, 1.0) * confidence * capture
```

Annualized ROI is visible but is intentionally not part of the sort score
yet. Andrew deferred velocity-weighted ranking until he trusts Sales/mo.

## 13. Acquisition and exit economics

File: `code/economics.py`.

### 13.1 Buy-side tax

Normal eBay planning tax:

```text
8%
```

Tax-free marketplace names:

```text
YAHOO_JP
PAYPAY_JP
```

`total_acquisition_cost()` applies tax to landed acquisition cost under the
existing planning model.

### 13.2 Exit route object

`ExitRoute`:

```text
channel
fee_rate
fixed_cost
net_proceeds
advantage_vs_ebay
```

### 13.3 Route configuration capabilities

Each route can use:

```text
fee_rate
fee_tiers
fixed_cost
min_value
max_value
categories
requires_graded
enabled
auto_enabled
```

### 13.4 Automatic selection

If `Listing.resale_channel == "auto"`:

1. enumerate configured routes;
2. apply eligibility rules;
3. evaluate net proceeds;
4. choose the maximum;
5. calculate advantage against configured eBay economics.

If a watchlist or portfolio row names a channel explicitly, that manual
override is honored.

Legacy configs without `resale_channels` remain eBay-only in auto mode.

### 13.5 Current configured exits

eBay:

```text
13.25% seller fee
```

Goldin Marketplace:

```text
8.3% current fee assumption
$100 minimum resale value
Pokemon Cards / Sports Cards / Video Games
requires professional grading
auto enabled
```

Heritage:

```text
20% provisional seller fee
manual only
```

Fanatics Collect:

```text
15% provisional seller fee
manual only
```

PSA Vault:

```text
enabled for eligible eBay acquisitions at landed cost >= $500
0% modeled acquisition tax
7% all-in exit cost
exclusive route once vault tax economics apply
```

### 13.6 Scoring integration

The same route now drives:

- current Edge Now;
- expected auction/BIN EV;
- expected net proceeds;
- ROI;
- Today Max Bid;
- Today Breakeven;
- open Portfolio marks.

Do not reintroduce separate sell-fee math in report or portfolio code.

## 14. Quality and trust gates

File: `code/quality.py`.

The system has two related but different concepts.

### 14.1 Tradeability gate

Hard blockers include:

- disputed value;
- suspicious price;
- mixed comp pool;
- ask-based estimate.

Action, Today, alerts, and digest share this gate. Category tabs retain rows
for inspection.

### 14.2 Evidence gate

Historical/learner evidence is stricter. It rejects:

- tradeability blocker;
- discovery query;
- regraded/per-listing value;
- collection-standard failure;
- fair value below floor;
- too few comps;
- too-low confidence.

Legacy `fair_history` rows are preserved but carry `trusted=NULL`; only
`trusted=1` can drive Movers and Portfolio fallback marks.

### 14.3 Output rules

After category fair-value floor and max-ROI checks:

- grails are always retained;
- graded Pokemon at effective PSA 5 or below are dropped under current local
  config;
- negative EV is dropped;
- negative ROI is dropped;
- a pure zero-bid auction is dropped;
- a hybrid is exempt only when Buy It Now is known;
- Yahoo is exempt from the bid-count rule because Buyee does not expose it.

`output_ok` is module-level on purpose. Do not move it back inside `main()`.
A function-local `_category` import once shadowed the module global and killed
every run for hours.

### 14.4 Collecting standards

Pokemon:

- must show 1st Edition / First Edition / 1st Ed / No Rarity;
- named vintage query exemptions: Topsun, Carddass, Movie Promo, No Rarity;
- grails exempt.

Video games:

- sealed/new-in-box/shrinkwrapped or professionally graded;
- grails exempt;
- category floor is $250 rather than the default $1,000.

Watches:

- visible in their category sheet;
- excluded from Action, profit alerts, and digest because modifiers,
  box/papers, and authenticity make automated valuation less trustworthy.

## 15. Excel report

File: `code/report.py`.

Possible sheets, in order:

1. `Today` when nonempty; moved to first and active.
2. `Action`.
3. `Pokemon Cards`.
4. `Sports Cards`.
5. `Video Games`.
6. `Watches`.
7. `Other`.
8. `Grails`.
9. `Crossover`.
10. `Portfolio`.
11. `Source Health`.
12. `Discovery`.
13. `Movers`.
14. `About`.

Empty optional sheets are omitted.

### 15.1 Today

End-of-day decision list:

- auctions ending within 24 hours, sorted by deadline;
- fresh BINs within 24 hours, sorted by score;
- EV floor $75;
- confidence floor 25%;
- centralized tradeability gate;
- Decide label: BID? / BUY? / OFFER?.

Columns now include:

```text
Best Exit
Net Proceeds
vs eBay
Max Bid
Breakeven
```

Max Bid leaves 15% target ROI. Breakeven is gray and represents zero profit.

### 15.2 Action and category sheets

Main columns:

```text
Rank, Pri, Type, Site, Title, Query, Price, Ship, Bids, Timing,
Fair Value, Trend 30d, Comps Val, Guide Val, #Comps, Exp Cost,
Expected Value, Edge Now, ROI, Sales/mo, Ann ROI, Capture, Conf, Score,
Best Exit, Exit Fee, Net Proceeds, vs eBay, Notes, Model Detail
```

Important columns:

- M:P valuation audit group is hidden/grouped.
- Notes is AC.
- Model Detail is hidden AD.
- Older docs saying Model Detail is Z are stale.

Action:

- no watches;
- centralized tradeability gate;
- duplicate cards collapsed;
- always includes top 50 ranked tradeable rows plus positive-EV/soon rows.

Category sheets:

- full uncollapsed research book;
- can show questionable rows that Action correctly excludes.

### 15.3 Dedupe

Rows collapse only when:

- same query;
- same listing type;
- same effective grade;
- overlapping subject tokens;
- title match at least 0.6;
- no variant conflict.

Auctions and BINs never merge. The retained row is best EV, then cheapest.
Notes show `+N more listing(s) ...`.

### 15.4 Crossover

Current allowlist:

```text
graders: CGC, BGS, SGC, BVG
categories: Pokemon Cards, Sports Cards
```

WATA/VGA games cannot enter. Disputed/nontradeable values cannot enter.

Default PSA fee tiers:

```text
<= $500      $25
<= $1,500    $75
<= $2,500    $150
above        $300
```

Regrade Profit is modeled edge minus grading fee. The risk that the crossover
comes back below the assumed shifted grade remains real.

### 15.5 Source Health

Columns:

```text
Source, Status, OK, Failed, Skipped, Freshness, Mode, Checked, Detail
```

Statuses:

```text
healthy, idle, disabled, degraded, cooling, failing, stale, empty
```

Parser calls now contribute separately, so `HTTP ok + parse failed` becomes
degraded rather than healthy.

### 15.6 Portfolio

`portfolio/portfolio.csv` columns:

```text
date_bought
description
query
cost_basis
date_sold
sale_proceeds
resale_channel
notes
```

Open positions:

- mark from this run's trustworthy fair values;
- fallback to latest `trusted=1` fair history;
- optimize exit unless manually pinned;
- show Best Exit;
- calculate net liquidation value, P&L, return, CAGR, and totals.

Closed positions use recorded net sale proceeds.

## 16. Alerts, digest, grails, and closer

### 16.1 Profit alerts

File: `code/alerts.py`.

Current configured gates:

```text
edge_now >= $150
roi >= 15%
roi <= 200%
confidence >= 15%
priority only
not discovery
not suspicious/nontradeable
```

Capture gate applies to BIN freshness, not all auctions. This was a critical
fix: time-decayed auction capture previously muted every useful early
auction alert.

An item is recorded in `alerts` only after a delivery channel succeeds.
Failed Telegram sends retry next run.

Telegram breaker is shared between alerts and digest and trips after three
consecutive failures.

### 16.2 Digest

File: `code/digest.py`.

Full scans only:

- top 25 profit opportunities;
- top 25 live-auction grails;
- HTML links;
- chunked below Telegram's message size.

No separate daily scheduler is needed; it rides the full scan schedule.

### 16.3 Grails

File: `code/grails.py`.

Current config has 42 grails. They are ordered by personal significance and
mapped to approximately 100 down to 40.

Matching:

- strict AND token matching;
- synonyms for signed/autograph, first/1st, gameboy/game boy, pack/booster;
- highest-score match wins;
- every discovered listing is tested;
- default minimum substantial price: $3,000;
- optional per-grail max price;
- grails bypass profit output filters and max-row trimming.

Grails are not synonymous with profitable flips.

### 16.4 Close tracker

File: `code/closer.py`.

After each live run:

- inspect recently ended observed eBay auctions by item ID;
- read actual winning bid plus known shipping;
- record into `closed`;
- mark unsold as 0 to stop endless rechecks;
- leave undetermined rows for retry;
- stop on challenge/breaker;
- limit currently 20 close lookups per run.

Exact item ID close matching is the primary calibration source. Legacy fuzzy
`match_closed()` remains as a secondary path when comps flow.

## 17. Database and learner

### 17.1 Current database

Path:

```text
database/history.db
```

Observed size:

```text
19,058,688 bytes
```

Current row counts:

```text
comps          6,951
fair_history   6,261
observations  19,453
closed           215
alerts            42
guide_cache        0
source_health     20
```

Tables:

```text
alerts(item_key PK, alerted_at)
closed(item_id PK, actual_price, closed_at)
comps(query, title, price, sold_date, url, site, scanned_at, comp_key, ...)
fair_history(query, ts, fair, n_comps, trusted)
guide_cache(query PK, value, ts)
guide_meta(key PK, value)
observations(item_id, site, query, title, listing_type, price, shipping,
             bids, end_time, fair, predicted_settle, hours_left,
             observed_at, n_comps, confidence, trusted)
source_health(source, run_at, mode, status, ok, failed, skipped,
              freshness_hours, detail)
```

Database migration code makes backups before material dedupe/quarantine.
Never delete those backups without Andrew's explicit approval.

### 17.2 Current learner state

`model/learned_params.json`:

```json
{
  "n": 0,
  "n_snapshots": 0,
  "filters": {
    "min_fair": 50,
    "min_comps": 3,
    "ratio_band": [0.1, 3.0]
  },
  "training_filter": {
    "joined": 1090,
    "dropped_fair_below_floor": 891,
    "dropped_no_evidence_recorded": 199
  },
  "ml": {
    "deployed": false,
    "benched_why": "cold start"
  }
}
```

This is deliberate. Historical closes were valued before the evidence and
valuation repairs. Do not relax filters to make a learned number appear.

Learner tiers:

- Tier 0: config `auction_settle_ratio=0.92`.
- Tier 1: at least 20 trustworthy distinct closed auctions; median
  actual/fair, plus bands when each has enough evidence.
- Tier 2: at least 150 distinct auctions; gradient boosting; GroupKFold by
  item; deploy only if it beats parametric by at least 3% and absolute CV MAE
  is at most 0.25.

`model.pkl` may remain on disk while `ml.deployed` is false. The engine obeys
the deployment flag/current parameters rather than assuming file existence
means use.

## 18. Current local configuration snapshot

`config.yaml` is ignored and is the live source of truth. Re-read it every
session because Andrew edits it.

Safe current summary:

```text
watchlist entries: 90
grails: 42
sites: ebay, yahoo_jp, goldin, heritage
marketplaces: EBAY_US, EBAY_GB, EBAY_DE
```

Algorithm:

```text
auction settle ratio              0.92
default resale channel            auto
eBay exit fee                     13.25%
Goldin exit fee                   8.3%
sales tax                         8%
vault minimum                     $500
vault all-in exit fee             7%
title-match floor                 0.55
outlier MAD k                     3
comp half-life                    30 days
late auction window               6 hours
close interpolation tau           24 hours
proxy lift                        2% per bid
proxy cap                         15%
sniper floor                      75% of settle
capture half-life                 12 hours
ungraded equivalent               PSA 5
guide skipped at                  8 strong comps
exact pool minimum                3 comps
mixed-pool confidence cap         25%
subject tokens                    2
```

Scraping:

```text
base HTML delay                   3.5s
parallel workers                  10
HTML timeout                      12s
challenge threshold               3
max results/query                 40
targeted exact queries/full       20
targeted exact queries/BIN         6
targeted results/query            60
close lookups/run                 20
international priority only      true
background comp warms/BIN          6
eBay HTML comps                  true
130point                         false
breaker failure threshold          3
breaker base cooldown             30m
breaker max cooldown              24h
```

Output:

```text
minimum EV                       $0
max rows                       1000
Today horizon                    24h
Today fresh horizon              24h
Today minimum EV                $75
Today confidence floor           25%
Max Bid target ROI               15%
```

Filters:

```text
default minimum fair value     $1,000
video-game minimum fair value    $250
maximum purchase price        $100,000
maximum ROI                      200%
Pokemon grade floor              PSA 5
seller feedback flag             <10
suspicious price ratio           <35% of resale
listing match floor              0.60
Pokemon eras only               true
games sealed or graded          true
```

Excluded words include:

```text
reprint, proxy, replica, custom card, digital, orica, fan made,
metal card, gold card, sticker decal, jumbo oversized, coaster,
you pick, pick your, choose your, repack, poster, art print,
chase box, set-break, set break
```

Japan:

```text
JPY/USD planning FX              0.0063
proxy fee                         $10
domestic shipping                  $8
international shipping            $35
insurance                           1%
FX spread                           3%
import duty planning assumption    15%
```

Alerts:

```text
enabled
priority only
$150 minimum current edge
15% minimum ROI
200% maximum ROI
15% confidence floor
macOS notifications and sound
Telegram configured
grail and digest branches enabled
```

Credentials exist locally but are intentionally omitted here.

## 19. Production acceptance and today's live evidence

### 19.1 Acceptance run before marketplace repair

Command:

```bash
caffeinate -im .venv/bin/python -B code/main.py \
  -c config.yaml \
  -o "reports/Opp Runs/acceptance_7-10_2026-07-25.xlsx"
```

Result:

```text
self-test: 105 passed
exit: 1
duration: 1m 48s
no actionable workbook because no live inventory reached scoring
```

Evidence:

- eBay OAuth succeeded.
- eBay Browse produced three consecutive HTTP 429 and opened a one-hour
  breaker.
- eBay sold HTML hit bot-challenge pages and opened a four-hour breaker.
- Yahoo/Buyee returned HTTP successfully for 20 priority queries but no
  `itemCard` schema.
- stale comp caches were used;
- no live listings existed;
- Source Health flagged eBay listings/comps and comp cache;
- the run correctly did not pad the workbook with fake rows.

API footer:

```text
ebay/html: 3 ok, 1 failed, 430 skipped
ebay/api: 3 failed, 419 skipped
yahoo_jp/html: 20 ok
oauth: 1 ok
```

This proved persistent breaker/source-health behavior and exposed the
marketplace work.

### 19.2 Repair validation

Goldin:

- current API schema fixture test;
- live isolated query returned three lots;
- production run now returns real inventory.

Heritage:

- current markup fixture test;
- live isolated query returned three listings;
- production narrow queries show legitimate zeroes without parser failure.

Excel:

- demo report wrote successfully;
- Action headers were programmatically checked:
  `Y=Best Exit`, `Z=Exit Fee`, `AA=Net Proceeds`, `AB=vs eBay`,
  `AD=Model Detail`;
- sample demo row selected `PSA Vault`, 7% fee, $5,272.27 net proceeds,
  $354.32 advantage versus eBay.

### 19.3 Completed production run after repair

At 22:22 a live full scan ran on the 114-test code and completed:

```text
duration: 1m 47s
exit: 0
report: reports/Opp Runs/opportunities_2026-07-25_22.22.xlsx
kept rows: 2, both personal-collection grails
Action rows: 0
alerts: 0
```

Both grail rows were Goldin sealed 1st Edition Pokemon packs. Both carried a
MIXED POOL note and were correctly blocked from profit alerts. Grails are
allowed into their own sheet even when not tradeable; the log phrase
`report: 2 actionable rows` is therefore slightly misleading because it is
counting kept grails, not two profit-ready decisions.

Evidence gate:

```text
172 valuations kept out of learner/history:
  discovery query x65
  fair value below trust floor x47
  listing-specific regrade x40
  no fair value x18
  too few matched comps x1
  outside collection standards x1
```

Targeted comp phase planned 20 exact searches and found zero pools with the
required three rows. The comp cache was approximately 57 hours old versus
the configured 48-hour freshness limit.

Source/API footer:

```text
ebay/html:             434 skipped
ebay/api:               30 failed, 392 skipped
ebay/oauth:              1 ok
goldin/api:              92 ok
goldin/parse:            92 ok
heritage/html:            6 ok, 3 failed, 83 skipped
heritage/parse:           6 ok
yahoo_jp/html:           20 ok
yahoo_jp/parse:          20 failed
pricecharting:           30 ok, 1 failed, 91 skipped
pokemontcg.io:            7 ok, 5 failed, 49 skipped
```

Source Health interpretation:

```text
Goldin                 healthy
eBay listings/comps    cooling
Yahoo/Buyee            degraded
Heritage               cooling
PriceCharting          cooling
comp cache             stale
130point/Fanatics      disabled
```

One additional inconsistency surfaced: Source Health labels PokemonTCG.io
disabled when no API key is configured, but the guide code still made
anonymous PokemonTCG requests (seven successes, five 500 errors, then
skips). Decide whether anonymous use is intentional; the source-health
enabled rule and runtime behavior should agree.

The scheduled 22:30 BIN sweep then respected the persisted breakers, made no
eBay calls, used stale caches, found no live inventory, and exited 1 in
three seconds.

## 20. Known issues and deliberately unfinished work

### P0 — eBay API failure fan-out

Healthy concurrency is useful, but breaker entry must be atomic. Current
behavior can hammer a rate-limited endpoint with many already-admitted calls.
Fix and test before another aggressive full scan if cooldowns persist.

### P0/P1 — Yahoo/Buyee is not producing inventory

Treat this as a transport/schema investigation, not a one-selector patch.
The landed economics are already complete and should be preserved.

### P1 — perform a clean acceptance scan after cooldown

The first acceptance run predated the repaired Goldin/Heritage config. The
22:22 run proves Goldin works but was contaminated by the eBay fan-out. After
fixing/letting cooldowns expire, verify:

- source-health statuses;
- listing counts by source;
- parser canaries;
- landed premiums;
- Today Best Exit;
- Max Bid/Breakeven;
- portfolio marks;
- alerts/digest behavior.

### P1/P2 — Heritage transport stability

The parser is validated and live-capable, but the 22:22 run had six good
requests followed by three transport failures and a cooldown. Determine
whether the failures are rate/politeness related, a bot response, or a
transient network issue. Keep the current conservative serial HTML lane and
do not increase Heritage request frequency.

### P1 — remote Git is behind

`origin/main` lacks `8fb14fc` and `6492601`. Push only after confirming the
new handoff and current run.

### P2 — Fanatics

Wait for a reachable first-party anonymous search surface. Do not use leaked
keys or account-only tokens.

### P2 — exit venue expansion

Add Probstein and COMC with verified:

- eligible categories/grades;
- seller fee tiers;
- fixed costs;
- shipping/handling;
- minimum item value;
- payout timing;
- vault/storage constraints.

The optimizer already supports most of this configuration shape.

### P2 — 130point / velocity

Without 130point, eBay comp dates are coarse fetch dates. Sales/mo and
annualized ROI are useful but not perfect. Do not fold annualized ROI into
the main Score until Andrew explicitly approves.

### P2 — PokemonTCG source-health/runtime mismatch

With no configured key, Source Health describes PokemonTCG.io as disabled,
but `PriceGuide` still attempts anonymous requests. The completed 22:22 run
made twelve real calls before its breaker state led to skips. Choose one
truthful behavior:

- anonymous access is supported: mark it enabled and document the limits; or
- a key is required: skip all calls when the key is missing.

Add a test connecting `_configured_sources()` to actual guide behavior.

### P3 — “actionable rows” log wording includes grails

The 22:22 workbook contained two retained grails and zero Action rows, while
the log said `report: 2 actionable rows`. The data policy is correct—grails
belong in the report and were barred from profit alerts—but the wording can
mislead an operator. Consider logging `kept rows` plus `tradeable rows`
separately.

### P2 — exact watchlist queries

Broad vintage sports searches still mix parallel card numbers during
discovery. Candidate explicit queries discussed historically include:

```text
Michael Jordan 1984 Star #288 / #7 / #26
Babe Ruth 1933 Goudey #149 / #53
George Mikan 1948 Bowman #69
Ted Williams 1939 Play Ball #92
Lionel Messi 2004 Megacracks #71 / #35 / #62
Ty Cobb T206 #150 / #460
```

Andrew wanted to sanity-check parallel Jordan sets before these are added.

### Operational caveats

- The assistant environment could not read `crontab -l` because of sandbox
  permission, so the historical schedule (full daily around 6pm, BIN every
  30 minutes) was not independently reverified during this handoff.
- Andrew manages Mac sleep himself and previously declined changing `pmset`.
- `Fix Mac Sleep.command` exists but should not be re-pitched unless missing
  overnight runs become a problem.

## 21. Historical bugs that must not return

Use this section as a regression-review checklist.

1. **Function-local import outage:** local `_category` import shadowed the
   global and killed every run. Keep `output_ok` module-level.
2. **Zero-bid hybrid fake bargain:** opening bid was treated as takeable.
   Use known BIN.
3. **Max Bid equaled Breakeven:** preserve target ROI margin.
4. **Vault fee stacking:** vault 7% replaces ordinary fee.
5. **Tax/exit mismatch:** vault tax exemption requires vault exit.
6. **Raw under graded query:** ungraded must be valued as PSA 5, not inherit
   query grade.
7. **CGC guide inflation:** price guides must route through effective grade.
8. **Impossible grades:** `CGC 85` must never become PSA 84.
9. **Seller adjectives as subject:** preserve subject candidate filtering.
10. **Accent corruption:** preserve Japanese-aware folding.
11. **Card-number mixing:** exact numbered query rejects unnumbered comps.
12. **Wrong year mixing:** old cards cannot use modern tribute comps.
13. **Excluded keywords only on listings:** blacklist must also screen comps.
14. **Duplicate comp URLs:** canonical item identity must collapse URL
    variants.
15. **Weak evidence poisoning learner:** preserve central evidence gate.
16. **Legacy fair history driving marks:** only `trusted=1`.
17. **Discovery/ask-based/mixed pool entering alerts:** preserve tradeability
    gate.
18. **Crossover games:** WATA/VGA must not enter the card Crossover sheet.
19. **Sealed Pokemon classified as games:** platform keywords before Pokemon,
    title keywords after Pokemon; “sealed” is not itself a game category.
20. **Challenge retry spam:** preserve run-wide tally and persistent cooldown.
21. **429 automatic retries:** never retry a 429 immediately.
22. **NULL guide cache poisoning:** network failure must not cache a miss.
23. **Empty report padding:** never resurrect rejected raw rows.
24. **Model leakage:** GroupKFold by auction item, not random snapshots.
25. **Snapshot-weighted settle median:** learner Tier 1 is per auction.
26. **Stale learned model after cold start:** always write parameters and
    obey deployment state.

## 22. Suggested first work session for the next system

### Step 1 — establish exact state

```bash
cd "/Users/alekas/Desktop/ebay opportunities"
git status --short
git log --oneline --decorate -10
tail -250 logs/scan.log
cat state/.breaker_state.json
ls -lt "reports/Opp Runs" "reports/BIN runs" | head -30
```

Do not run a fresh full scan while eBay is still in cooldown just to see it
fail again.

### Step 2 — run offline safety checks

```bash
.venv/bin/python -m unittest discover -s code -p "test_*.py" -v
.venv/bin/python -B code/main.py --demo -o /tmp/scanner-next-session.xlsx
```

Expected: 114 tests, OK.

### Step 3 — fix the API admission race

Relevant file:

```text
code/scrapers/base.py
```

Relevant tests:

```text
TestApiThrottling in code/test_fixes.py
```

Add a simultaneous-worker regression test. Verify the wire-call counter, not
only `API_STATS`.

Potential safe approach:

- per-lane lock around “is this request allowed to start?” and failure-state
  update;
- reservation counter that caps outstanding probes while health is unknown;
- after a success, normal concurrency can resume;
- after the third consecutive failure, queued/unstarted work becomes
  `skipped`.

Do not serialize all healthy eBay API calls unless necessary; that would
erase the performance improvement.

### Step 4 — investigate Yahoo in a browser

Capture:

- final URL;
- response HTML;
- XHR/fetch endpoints;
- result JSON schema;
- cookies/locale/region requirements;
- whether inventory is still Yahoo/PayPay;
- stable item IDs and current price;
- listing type;
- end time if available.

Implement a parser fixture first, then live smoke.

### Step 5 — clean acceptance

Run only after cooldown and fixes. Expected checks:

```text
self-test 114
eBay call count respects 3-failure ceiling if still limited
Goldin parse healthy and returns inventory
Heritage parse healthy even when query has zero live lots
Yahoo either healthy or explicitly degraded
Source Health workbook row matches footer evidence
Best Exit/fees/net/vs-eBay populated
Max Bid leaves configured ROI
Breakeven is higher than Max Bid
buyer premiums included in landed cost
```

### Step 6 — inspect the workbook with Andrew

Ask him to focus on:

- Today list length;
- whether Goldin exits are operationally realistic;
- Max Bid conservatism;
- exact-card fallback notes;
- Crossover risk;
- suspicious and disputed rows;
- whether Portfolio Best Exit matches how he would actually sell.

Use his feedback to tune config before changing algorithms.

## 23. Coding and editing conventions

- Edit the real Desktop project, not a scratch copy.
- Preserve unrelated user changes.
- Use targeted patches.
- Read `config.yaml` fresh, but never echo secrets.
- Keep config ignored.
- Add a regression test for every material bug.
- Prefer exact examples from Andrew's reports.
- Run unit tests and demo.
- Use live network smoke tests only when necessary and respectful.
- Respect persistent breakers; do not delete breaker state merely to force a
  request.
- Do not mutate the real database from ad hoc scripts. Put migrations in
  `code/db.py`, make backups, test on temporary copies, then let the native
  app run them.
- Update this handoff when architecture, current source status, report
  columns, test count, or Git state changes.
- A code implementation is not done until the one-file-per-run Excel output
  reflects it.

## 24. Quick reference: most important files

```text
code/main.py
  Orchestration, self-test, lock, source pipeline, output filters, lifecycle.

code/valuation/engine.py
  Fair value, auction/BIN expected cost, confidence, EV, route integration.

code/valuation/comps.py
  Grade/subject/year/number/variant/language comp hygiene.

code/valuation/price_guide.py
  PriceCharting/PokemonTCG routing, cache, backoff.

code/economics.py
  Acquisition tax helpers and automatic exit optimizer.

code/models.py
  Listing landed-cost equation and all shared dataclasses.

code/quality.py
  Central actionability and evidence policy.

code/report.py
  Excel sheets, Max Bid/Breakeven, exit columns, formatting.

code/scrapers/base.py
  HTTP transport, API statistics, persistent breakers, cookies.

code/scrapers/ebay.py
  Primary marketplace and sold comps.

code/scrapers/goldin.py
  Current public Goldin lots service.

code/scrapers/heritage.py
  Sports Heritage current search/parser.

code/scrapers/yahoo_jp.py
  Broken-currently Buyee transport plus complete JP cost population.

code/source_health.py
  Persistent per-source readiness snapshot.

code/db.py
  SQLite schema, migrations, identity, persistence, calibration.

code/learner.py
  Trust-filtered settle ratio and guarded ML deployment.

code/test_fixes.py
  114 regression tests pinned to real historical failures.

config.yaml
  Ignored live strategy/settings/credentials. Re-read; never commit.

logs/scan.log
  Ground truth for current production behavior.
```

## 25. Definition of “picked up where we left off”

A new system is correctly oriented when it can state all of the following:

- The scanner is not merely eBay scraping; it is a decision and evidence
  pipeline.
- Goldin and Heritage were just restored and are live.
- Yahoo/Buyee is currently broken at parse/transport level.
- Fanatics and 130point are deliberately disabled.
- eBay is in API/HTML cooldown and still has an API in-flight breaker race.
- The automatic exit optimizer is already built and integrated everywhere.
- PSA Vault economics are exclusive.
- Buyer premiums/minimums are part of landed cost.
- Max Bid and Breakeven are separate.
- Legacy fair history and old closes are quarantined.
- The learner is intentionally cold at `n=0`.
- There are 114 passing tests.
- Local Git contains two unpushed implementation commits beyond
  `origin/main`.
- The first engineering task is atomic API call admission, followed by Yahoo
  repair and a clean production acceptance scan.

If any of those statements changes, update this document immediately.

---

## 26. Session 2026-07-26 — Goldin cost completion and platform expansion

This section supersedes older statements in this handoff that say Goldin is
fully modeled, Fanatics still uses Algolia, ALT is not started, Pristine is
absent, fixed-price orchestration is eBay-only, or the suite has 114 tests.

### User direction

The user asked whether Goldin was working perfectly, then authorized work in
this order:

0. Build the capability-based connector framework and cross-platform dedupe.
1. Start Fanatics Collect.
2. Start ALT.
3. Add Pristine Auction.

The user explicitly allowed as much time as necessary and walked away.

### Goldin audit and correction

Goldin's live transport/parser was verified before modifying it:

- `POST https://d1wu47wucybvr3.cloudfront.net/api/lots_v2`
- Live queries including `Topsun Charizard` returned real lot IDs, titles,
  current hammer prices, bid counts, end times, URLs, and 22% premiums.
- Existing telemetry was healthy and recent production logs contained
  `goldin/api: 92 ok`, `goldin/parse: 92 ok`, and 398 raw Goldin listings.

Goldin was nevertheless not financially complete. The old model applied the
lot's percentage premium and configured $19 minimum but omitted published
shipping and insurance. `code/scrapers/goldin.py` now applies:

- $6 single-card shipping when price realized is below $1,000;
- $19 single-card shipping at/above $1,000 and a $19 shipping floor for
  non-card lots whose exact fulfillment cost is not present in search;
- 0.9% insurance on price realized (hammer plus buyer premium);
- the existing lot-provided buyer premium, normally 22%;
- the existing $19 minimum buyer fee.

The corresponding ignored live values now exist under
`marketplace_costs.goldin` in `config.yaml`. Focused canaries cover both
shipping tiers. A post-change live smoke returned three real lots and exact
landed-cost stacks. Example: a $2,500 hammer becomes $3,096.45:
$2,500 + $550 premium + $27.45 insurance + $19 shipping.

Important limitation: shipping is selected from the listing's current
price-realized tier and then treated as fixed by the generic inverse bid
equation. Crossing the $1,000 threshold can change shipping by $13. Large or
heavy non-card lots can cost more than the $19 floor. Verify near-threshold
bid ceilings and non-card shipping manually when material. The generic
landed-cost model now has `insurance_on_buyer_fee`; its inverse is exact for
Goldin's percentage or minimum premium within the selected shipping tier.

### Capability-based connector orchestration

`BaseScraper` now has a `capabilities` set and `supports()` method. Supported
lane names are:

- `auctions`
- `fixed`
- `sold`

Current declarations:

- eBay: auctions, fixed, sold
- Goldin: auctions
- Heritage: auctions
- Yahoo/Buyee: auctions (its existing method returns the mixed
  Yahoo-auction/PayPay feed and remains one request lane)
- 130point: sold
- Fanatics Collect: auctions, fixed
- ALT: auctions, fixed
- Pristine Auction: auctions

`main._search_marketplaces()` is now the single orchestration point. Full
mode calls every configured auction and fixed connector. Auction mode calls
all auction connectors. BIN mode calls every fixed connector, not just eBay.
eBay keeps its `intl` argument and Yahoo keeps `query_ja`.

This matters because the former `main.py` explicitly called
`ebay.search_fixed()` and could never use Fanatics/ALT fixed listings even if
their connector worked and was enabled.

### Conservative cross-platform dedupe

`Listing` gained `canonical_asset_id`. `_listing_identity()` and
`_dedupe_listings()` in `main.py` now use this priority:

1. explicit source-independent physical asset identity;
2. source-namespaced native listing ID;
3. source-namespaced canonicalized URL.

If the same physical asset is cross-listed, the route with the lower known
landed cost is kept. Crucially, title similarity is never used for physical
dedupe. Two copies with identical card/set/grade titles remain two listings.

Authorized-feed connectors derive IDs such as `psa:12345678` only when both
a grader and certificate number are supplied, or accept an explicit
`canonical_asset_id`. This permits safe ALT/eBay or other cross-list collapse
without confusing two distinct slabs.

### Fanatics Collect implementation

The legacy `fanatics_collect.py` Algolia adapter was removed. It contained an
old public app ID and instructions to extract a browser search key. That path
was brittle, had already rotated, and is not the approved integration model.

The replacement inherits `AuthorizedFeedScraper` and supports:

- an explicitly authorized read-only JSON endpoint;
- a user/platform-supplied local JSON export;
- auction and fixed-price inventory;
- current price/bid, listing ID, URL, end time, image, bid count;
- fee, shipping, insurance, best-offer, marketplace, currency;
- grader/certificate cross-list identity.

It makes zero network requests unless `authorized: true` and `endpoint` are
both configured. A local `feed_file` does not contact Fanatics.

Fanatics acquisition defaults:

- auctions: 20% buyer premium;
- Buy Now/fixed: no buyer premium;
- item feed fields may override the defaults;
- shipping remains item/feed-specific.

Fanatics remains intentionally absent from the live `sites:` list because no
authorized endpoint/export is configured. This is an access dependency, not
unfinished parser/orchestration work.

### ALT implementation

`code/scrapers/alt.py` now uses the same permission-gated boundary. It
supports auction and fixed inventory, authorized endpoint/local export,
normalized landed-cost fields, and canonical certificate identity.

ALT acquisition defaults:

- auctions: 20% buyer premium;
- fixed: no auction premium;
- feed fields may override.

ALT is also absent from live `sites:` until approved access or an export is
configured. It will make no marketplace request in the current state.

### Shared authorized feed contract

`code/scrapers/authorized_feed.py` is the reusable implementation. Expected
top-level JSON is:

```json
{"items": [{"title": "...", "url": "...", "current_price": 100}]}
```

The full field/alias/access contract is documented in
`docs/PLATFORM_ACCESS.md`. Endpoint calls use GET query parameters:

- `q`
- `type` (`auction` or `fixed`)
- `limit`

Bearer tokens, configurable API-key headers, local relative file paths, and
environment overrides are supported. `secrets.example.yaml` now contains the
Fanatics and ALT authorization shapes. Old Algolia app/search key examples
were removed.

Environment variables:

- `CARD_SCANNER_FANATICS_ENDPOINT`
- `CARD_SCANNER_FANATICS_ACCESS_TOKEN`
- `CARD_SCANNER_ALT_ENDPOINT`
- `CARD_SCANNER_ALT_ACCESS_TOKEN`

### Pristine Auction implementation

`code/scrapers/pristine.py` is a live public-search connector and is enabled
in the ignored local `config.yaml`.

Endpoint:

```text
GET https://www.pristineauction.com/auction/search/
```

Query parameters include `term`, `category`, `sort_method=ending-soonest`,
and `per_page`. Transport uses the shared `curl_cffi` Chrome impersonation,
serialized polite HTML lane, circuit breaker, cookies, and a Pristine-specific
2.5-second base delay.

Live markup verified 2026-07-26:

```html
<div class="row product"
     aria-label="Auction item"
     data-pristine-product-venue-id="12868123"
     data-pristine-title="...">
  <a class="title" href="/a12868123-...">...</a>
  <p class="high-bid" data-high-bid="6.35">...</p>
  <span class="end-time" data-pristine-end-time="1785121200">...</span>
</div>
```

The parser extracts the canonical lot link/ID, full title, current bid, Unix
end timestamp, and image. It applies the published 17% buyer premium.
Pristine shipping varies by lot, value, location, and invoice combination;
`marketplace_costs.pristine.shipping_estimate` is explicit and currently
zero. This can understate delivered cost and must be set conservatively for
the user's normal fulfillment behavior.

Live smoke after implementation:

- query: `charizard`
- five requested, five returned
- real lot IDs, prices, end times, URLs, and titles
- a $100 equivalent would land at $117 before variable shipping
- a legitimate `No results found` page is parser-healthy, not a schema error

### Source health and reporting

`code/source_health.py` now includes:

- `pristine/listings`
- `alt/listings`
- authorization/feed/access/parse endpoint mappings for Fanatics and ALT

Fanatics/ALT show disabled unless both selected in `sites` and access-ready.
Pristine reports HTML/parse health. `report.py` recognizes display labels for
Fanatics Collect, ALT, and Pristine Auction.

### Tests and validation

New regression coverage includes:

- Goldin low/high shipping tiers and insurance;
- capability advertisement;
- generalized fixed-price orchestration;
- native vs canonical-asset dedupe;
- identical-title distinct-copy preservation;
- normalized Fanatics auction fees/certificate identity;
- normalized ALT fixed-price economics;
- strict permission gating with an assertion that no network call occurs;
- Pristine live schema and 17% premium.

The complete suite passed after the main implementation:

```text
Ran 135 tests in 1.158s
OK
```

Later orchestration, exact Goldin insurance, non-card shipping-floor, and
local-export recall tests were added. Final validation:

```text
Ran 140 tests in 1.277s
OK
```

### Files added

- `code/scrapers/authorized_feed.py`
- `code/scrapers/alt.py`
- `code/scrapers/pristine.py`
- `docs/PLATFORM_ACCESS.md`

### Files materially changed this session

- `code/models.py`
- `code/main.py`
- `code/scrapers/base.py`
- `code/scrapers/ebay.py`
- `code/scrapers/goldin.py`
- `code/scrapers/heritage.py`
- `code/scrapers/yahoo_jp.py`
- `code/scrapers/point130.py`
- `code/scrapers/fanatics_collect.py`
- `code/scrapers/__init__.py`
- `code/source_health.py`
- `code/report.py`
- `code/test_fixes.py`
- `config.yaml` (ignored live configuration)
- `secrets.example.yaml`
- `docs/README.md`
- `docs/FEATURES.md`

There were already uncommitted PriceCharting/eBay sold-pacing changes at the
start of this work. Preserve them. Do not discard or reset the working tree.
No Git commit was made during this platform-expansion session.

### Immediate operational next steps

1. Run one full scan with Pristine enabled and review:
   source health, raw counts by source, relevance/filter reasons, and
   Pristine shipping sensitivity.
2. Put a conservative normal Pristine fulfillment estimate into
   `marketplace_costs.pristine.shipping_estimate` if the user normally ships
   individual wins.
3. Request documented read-only inventory access/export capability from
   Fanatics and ALT using `docs/PLATFORM_ACCESS.md`.
4. After credentials/export arrive, add `fanatics_collect` and/or `alt` to
   `sites`, run one-query smokes, verify fees and canonical asset IDs, then
   expand to the full watchlist.
5. Commit only when the user asks; include both the previously uncommitted
   pacing work and this session's integration work unless the user requests
   separate commits.

## 2026-07-26 eBay live-result depth

The shared `scraping.max_results_per_query: 40` setting was regularly
truncating the user's primary transaction venue. eBay now has a connector-
specific override:

```yaml
scraping:
  max_results_per_query: 40
  max_results_per_query_by_site:
    ebay: 500
```

Secondary/API-fragile venues therefore remain at 40. eBay Browse search uses
its maximum 200-item page size and follows the response `next` pagination
until the configured 500-result ceiling or the natural end of results. The
ceiling applies per marketplace and per lane (auction versus fixed price).
Priority searches may cover EBAY_US, EBAY_GB, and EBAY_DE; ordinary searches
are usually EBAY_US-only under `international_priority_only`.

The earlier misspelling-search and sold-comps limits are separate and were
not changed. Sold comps still use paced public HTML rather than Browse API.

Validation:

- a live US-only `Disney Chrome 2023` auction search returned 500 listings,
  proving pagination continued beyond both the old 40-row scanner cap and
  eBay's 200-item page;
- the end-to-end demo succeeded;
- the complete suite now reports `Ran 142 tests ... OK`, including regression
  coverage for connector-specific caps and constant-size 200/200/200 page
  requests (the final response is locally sliced to the configured ceiling).

## 2026-07-26 corrected full run, workbook audit, and exact resume state

This section is the most recent operational truth. It records the work done
after the platform-expansion section above, including a live production run,
one blocking crash fix, a complete workbook audit, and deeper eBay Browse API
pagination. Read this section before changing code or trusting the newest
report.

### User goal and current priority

The user buys collectibles primarily on eBay. The scanner should maximize
recall across eBay and other auction venues, then narrow the inventory using
high-quality identity matching, exact sold evidence, landed-cost economics,
collection standards, and decision gates. The desired output is one Excel
workbook per run that is broad enough to surface real opportunities but safe
enough that `Today` and `Action` can guide actual bids.

The latest run proved that marketplace collection is no longer the immediate
bottleneck: the scanner collected thousands of live listings and produced a
populated workbook. The next engineering bottleneck is **valuation identity**.
Broad query-level comp pools are still being assigned to materially different
objects, parallels, and product classes. Do not respond to this by simply
lowering the report floors; that would increase junk without fixing value
precision.

### The first live attempt exposed and fixed a deterministic crash

The user launched `Run Scan.command` at approximately 11:48 CDT. Monitoring
`logs/scan.log` revealed:

```text
UnboundLocalError: local variable 'key' referenced before assignment
code/main.py line 625
relevant.append((key, listing))
```

This came from the new listing-identity/dedupe refactor. The relevance loop
appended `(key, listing)` without assigning `key` on that path. The fix in
`code/main.py` is:

```python
key = _listing_identity(listing)
relevant.append((key, listing))
```

The already-running Python process had loaded the old module, so applying the
patch could not repair that process in memory. The user was asked to stop it.
The sandbox could identify its PID but could not signal the user's process
because macOS returned `operation not permitted`. After the stale process
stopped, the corrected scan was launched from the project root with:

```bash
bash './Run Scan.command'
```

Regression status immediately after the crash fix:

```text
Ran 140 tests
OK
```

The crash did not recur in the corrected full run.

### Corrected production run

Run start:

```text
2026-07-26 approximately 11:54 CDT
mode=all
demo=False
```

Run completion:

```text
exit=0
duration=19m 47s
```

Workbook:

```text
/Users/alekas/Desktop/ebay opportunities/reports/Opp Runs/
opportunities_2026-07-26_11.54.xlsx
```

The scanner's own final line was:

```text
wrote 86 kept opportunities and 4193 research explanations
```

The workbook opened successfully through the bundled spreadsheet runtime.
Every sheet was imported and rendered. There were no `#REF!`, `#DIV/0!`,
`#VALUE!`, `#NAME?`, or `#N/A` formula errors.

### Exact scan funnel

Marketplace collection and relevance:

```text
7,229 raw listing hits
4,209 relevant listings
4,209 valued listings
```

Raw source distribution:

```text
eBay       6,486
Goldin       405
Pristine     338
```

Relevance/identity removal reasons:

```text
duplicate within query       1,052
title match below threshold    734
wrong subject                  313
grade conflict                 308
language conflict              300
excluded keyword               210
variant conflict                58
over global price cap           45
```

Report classification waterfall:

```text
raw marketplace hits      7,229
after relevance           4,209
after valuation           4,209
fair-value-floor removals 3,813  -> 396 remain
ROI-ceiling removals         84  -> 312 remain
collection removals           8  -> 304 remain
output-economics removals    218 -> 86 remain
row-cap removals               0 -> 86 final kept
```

Decision-only quarantine:

```text
70 of the 86 kept rows blocked from Action/Today/alerts
16 classified decision-tradeable
```

The 70 quarantine reasons were:

```text
disputed valuation  41
discovery query     21
mixed comp pool      4
suspicious listing   4
```

`Research-Filtered` contains 4,193 explanation rows:

```text
Fair-value floor          3,813
Output economics            218
ROI sanity ceiling           84
Decision-only quarantine     70
Collection standards          8
```

Research rows by marketplace:

```text
eBay       3,717
Goldin       176
Pristine     300
```

The dominant removal was the fair-value floor. The configured category floors
are intentionally substantial: generally $1,000 for Pokemon, sports, watches,
and other collectibles, and $250 for video games. Thousands of broad-search
hits were ordinary low-value objects. Lowering the floors is not the first
change to make.

### Evidence gate and comp hygiene

The sold-comp junk screen ignored 339 excluded comp rows such as reprints and
`you pick` listings that would otherwise have influenced fair values.

The evidence gate kept 4,064 valuations out of learner/history:

```text
fair value below trust floor  1,583
discovery query               1,389
outside collection standards   470
listing-specific regrade        453
too few matched comps           127
suspicious listing               22
disputed valuation                17
no fair value                      3
```

This quarantine does not remove browse rows from the workbook. It protects
fair-history, trends, portfolio marks, and the learner from contaminated
values.

The closer found no newly trustworthy closes:

```text
0 real closes settled
0 unsold
20 pending retry
0/20 trustworthy closes
```

The hand-tuned `auction_settle_ratio` therefore remains active.

### Source health from the corrected run

#### eBay live listings

Healthy:

```text
ebay/api     422 OK
ebay/oauth     1 OK
```

The persisted Source Health sheet groups these as 423 healthy listing
operations with zero failures. Live inventory came from the official Browse
API, not HTML fallback.

#### eBay sold comps

Cooling:

```text
ebay/html 196 skipped
```

The earlier bot challenge had opened the cross-run sold-HTML breaker. The
scanner correctly used stale cached comps rather than continuing to hammer the
site. This lane is distinct from live Browse API inventory.

The comp cache held:

```text
6,951 rows
approximately 70.8 hours old
configured freshness limit: 48 hours
status: stale
```

Most established queries had usable stale pools. A few newer/broader queries
had zero sold comps. Do not interpret a healthy eBay listing lane as a healthy
eBay sold-comps lane.

#### PriceCharting

Healthy:

```text
635 successful calls
0 failures
```

The paid API request-control work behaved correctly: one serialized wire lane,
paced requests, cached product payload reuse, grade-specific values kept
separate, and no rate-limit failure in the full run. The valuation phase was
slow by design but stable.

PriceCharting field names such as `loose-price`, `cib-price`, `new-price`,
`graded-price`, `box-only-price`, and `manual-only-price` are overloaded for
graded-card rungs in this integration. The grade-routing log can therefore
look strange without necessarily being wrong. The dangerous issue is product
identity: the correct grade rung on the wrong/broad product is still a bad
valuation.

#### PokemonTCG.io

Degraded and then circuit-broken:

```text
7 successful
10 failed
348 skipped after breaker
```

Failures were HTTP 500s and one timeout. After three consecutive failures the
guide correctly disabled itself for the rest of the run.

#### Goldin

Healthy:

```text
goldin/api    92 OK
goldin/parse  92 OK
```

Goldin returned 405 raw live listings across all queries. Landed economics use
the lot-provided premium, normally 22%, with the configured $19 minimum,
single-card shipping tiers, non-card shipping floor, and 0.9% insurance on
hammer plus buyer premium.

Only two Goldin rows were visible in the final display, both in Grails.
Goldin did not generate a decision-grade Action row in this run.

#### Pristine

Degraded but substantially working:

```text
pristine/html  88 OK, 4 failed
pristine/parse 88 OK
```

Pristine returned 338 raw listings. It often returned zero for highly exact
vintage PSA queries, but broader searches produced meaningful inventory:

```text
1999 1st Edition Pokemon Set       5
1999 1st Edition Pokemon Jungle    2
1999 1st Edition Pokemon Fossil    4
Ichiro 2001                       40
Victor Wembanyama Prizm 2023      10
Batman                            40
Bowman Chrome Auto Refractor      40
```

Four transient HTTP 403s did not produce a permanent run-wide failure.
Pristine contributed no surviving visible opportunity rows. Its configured
shipping estimate remains zero, which can understate landed cost and must be
made conservative before treating Pristine opportunities as actionable.

#### Yahoo Japan / Buyee

Parser failure:

```text
yahoo_jp/html  20 OK
yahoo_jp/parse 20 failed
```

Pages fetched, but the current markup contained none of the expected item-card
schema. Every query returned zero normalized auctions. This is a parser repair
task, not a network-access task.

#### Heritage

Cooling:

```text
heritage/html 92 skipped
```

Three earlier 403s opened the breaker through approximately 13:49 CDT. The
corrected run did not keep retrying it.

#### Fanatics Collect and ALT

Both connectors remain implemented but disabled because authorized access or
a normalized local export has not been configured. Do not invent endpoints or
scrape protected surfaces. Use `docs/PLATFORM_ACCESS.md` to request read-only
API/export access.

#### Telegram

```text
2 successful operations
```

One fresh grail candidate was sent:

```text
2023 Topps Chrome Disney The Beast 100-Year Diamond Refractor
asking $5,000 versus scanner fair $1,070
```

This was a grail/freshness message, not a positive-EV purchase recommendation.
The ask was far above the estimated fair value.

### Workbook structure and row counts

The workbook contained 12 sheets:

```text
Today
Action
Filter Waterfall
Research-Filtered
Pokemon Cards
Sports Cards
Watches
Other
Grails
Source Health
Discovery
About
```

There was no Video Games sheet because no video-game listing survived the
final output classification.

Visible data rows:

```text
Today                 2
Action                2
Pokemon Cards         3
Sports Cards          1
Watches               5
Other                14
Grails               18
Discovery            21
Research-Filtered 4,193
```

The apparent gap between 86 kept rows and the 62 visible category/Grails/
Discovery rows is explained by the Grails display cap. The report keeps all
grail candidates internally but `_grails_tab` shows only the cheapest five
listings per grail. Twenty-four kept grail copies were therefore intentionally
not displayed.

Workbook rendering quality was generally good:

- headers, number formats, conditional formats, hyperlinks, and freeze panes
  rendered successfully;
- `Today`, `Action`, category tabs, `Grails`, `Source Health`, `Discovery`, and
  `About` were readable;
- `Research-Filtered` was necessarily very wide but usable;
- `Filter Waterfall` is functionally correct but visually unwieldy because
  the fair-floor and ROI reason strings create extremely tall wrapped rows.
  A future report cleanup should aggregate those reasons by category/range
  rather than writing every distinct dollar value into one cell.

### Critical valuation findings: do not buy directly from this Action tab

The systems run was successful. The purchase recommendations were not yet
precise enough to trust.

`Today` and `Action` contained two Disney auctions:

```text
1. 2023 Topps Chrome Disney 100 Cinderella
   Pink Refractor /399 PSA 10
   current price $415 + $4.99 shipping
   fair value $1,069.60
   expected value $383.89
   edge now $474.29
   ROI 62.85%
   confidence 56.7%
   max bid $859
   breakeven $989

2. 2023 Topps Chrome Disney 100 Escape from a Plane / Toy Story
   Black /10 PSA 10
   current price $456 + $4.99 shipping
   fair value $1,069.60
   expected value $344.98
   edge now $430.01
   ROI 53.09%
   confidence 56.5%
   max bid $859
   breakeven $989
```

Both materially different parallels received the exact same fair value,
comps value, guide value, and six-row evidence pool:

```text
fair value  $1,069.60
comps value $950
guide value $1,134
n_comps     6
model       blend w_comps=0.35, agreement=84%
```

Other Disney variants from `/399` through `/5`, `/10`, `/25`, `/50`, `/99`,
and `/100` also inherited $1,069.60. This proves the model valued the broad
`Disney Chrome 2023 PSA 10` pool, not each exact card and parallel. These rows
escaped the mixed-pool quarantine because the evidence sources agreed with
each other at the wrong level of specificity.

Do not bid to the workbook's $859 Max Bid without independent exact-card
verification.

### Watch false positives

The Watches tab exposed missing product-class and reference gates:

```text
Patek Philippe Ref 1593 platinum dial for restoration
  priced as if comparable to a complete Patek watch

Audemars Piguet Royal Oak Day Date Moonphase dial
  priced as if comparable to a complete watch

Patek Philippe genuine platinum buckle / strap-band part
  priced as if comparable to a complete watch

Patek Philippe Calatrava Travel Time 4864R
  valued from the broader/different Patek Philippe World Time query

Audemars Piguet Royal Oak 15400
  surfaced under Royal Oak Chronograph despite 15400 not being the same
  chronograph reference family
```

The buckle row was marked mixed pool, but the two dial rows and model-family
mismatches were not reliably quarantined. Watches are excluded from the
Action tab by design, which limited immediate harm, but the Watches sheet
itself is not purchase-grade.

Required watch identity controls:

- reject or browse-quarantine titles containing `dial`, `buckle`, `strap`,
  `band`, `bracelet`, `parts`, `movement`, `case`, `bezel`, `hands`, and
  `for restoration` unless the query explicitly requests that component;
- require complete-watch evidence for complete-watch valuation;
- extract and match reference numbers;
- treat Travel Time, World Time, Day-Date Moonphase, and Chronograph as
  different complications/model families;
- never route watch resale through PSA Vault.

### Superman and broad collectible false positives

The `Other` tab assigned approximately $2,821 to incompatible objects under
`Superman 1940`, including:

```text
1940 Superman Gum PSA card
R146 novelty premium coupon
1940 gum wax-pack wrapper
Action Comics #22
McFarlane modern seven-inch Superman figure
SGC-graded Superman card
```

The $18 modern McFarlane figure was modeled against a $2,821 broad pool.
Several rows were marked disputed and/or suspicious, so the unified
tradeability gate kept them out of Action. That quarantine is working, but the
identity layer should prevent the valuation from being constructed at all.

Required object-class controls:

- trading card versus comic book;
- card versus wrapper/pack/coupon;
- vintage original versus modern tribute/reproduction;
- figure/toy versus paper collectible;
- signed jersey versus jersey card versus ordinary autograph card;
- sealed game versus loose cartridge versus unrelated merchandise.

### Discovery behavior

Discovery rows are correctly labeled browse-only, but their values demonstrate
why broad pools must never become bid targets. For example, the broad
`Upper Deck Authenticated Signed Jersey` pool assigned the same $1,085 value
to:

```text
full signed jerseys
jersey autograph cards
ordinary game-used jersey cards
golf club/driver autograph cards
modern Upper Deck hockey cards
```

The `discovery query` quarantine blocked all 21 Discovery rows from decisions.
Keep that protection.

### Recommended next engineering sequence

The next instance should preserve all current work and, when the user switches
back to scanner quality, implement the following in this order:

1. **Object-class identity before valuation**
   - Classify complete watch, watch component, trading card, comic, wrapper,
     pack, coupon, figure/toy, jersey, jersey card, video game, and generic
     memorabilia.
   - A listing must match the query's object class or become browse-only.

2. **Exact card identity for decision rows**
   - Extract set/year, card number, subject, parallel/color, autograph/relic
     state, serial-number denominator, grader, and effective grade.
   - `/399` and `/10` cannot share a decision-grade comp pool merely because
     both are Disney Chrome PSA 10.
   - Card number and parallel conflicts should hard-block Action.

3. **Broad-pool quarantine**
   - Query-level guide/comps without exact identity may populate Discovery or
     Grails, but not Action/Today/alerts/learning/portfolio marks.
   - Agreement between two broad sources is not sufficient evidence.
   - Add an explicit `identity_specificity`/`exact_product_match` attestation
     to valuation trust.

4. **Watch-specific reference and component gates**
   - Add the part-token exclusions and reference-family matching described
     above.
   - Configure realistic watch resale channels rather than PSA Vault.

5. **Fresh sold evidence**
   - Let the eBay sold-HTML cooldown expire.
   - Refresh the comp cache gradually under the existing one-at-a-time pacing.
   - Do not promote stale broad comps to Action merely because PriceCharting
     returns a compatible-looking product.

6. **Repair secondary source gaps**
   - Yahoo/Buyee parser for current markup.
   - Heritage access/breaker behavior after cooldown.
   - Conservative Pristine shipping.
   - Authorized Fanatics and ALT access when the user obtains it.

Do **not** begin by lowering fair-value floors or disabling the ROI ceiling.
Those gates removed large volumes of obvious low-value and impossible rows.

### eBay live-result cap expansion completed after the audit

The user emphasized that eBay is the primary transaction venue and asked that
live listing collection not regularly stop at a small scanner cap.

Before this change:

```text
scraping.max_results_per_query: 40
```

That value applied to every connector. For eBay it meant 40 per marketplace,
per query, per lane:

```text
40 auctions
40 fixed-price
```

Priority searches can include EBAY_US, EBAY_GB, and EBAY_DE, which is why
some prior log counts exceeded 40 even though each individual marketplace
request was capped at 40.

The current live configuration is:

```yaml
scraping:
  max_results_per_query: 40
  max_results_per_query_by_site:
    ebay: 500
```

`code/main.py` now resolves the configured limit separately for each
connector. Secondary venues still receive 40. eBay receives 500.

`code/scrapers/ebay.py` now paginates Browse API results:

- eBay allows 1-200 items per page;
- search results expose a `next` URL/indicator;
- the API requires non-zero `offset` to be a multiple of `limit`;
- the connector therefore keeps a constant 200-item page size while
  paginating;
- it advances from response `offset + limit`;
- it stops when the configured ceiling is reached, `next` disappears, the
  page is empty, the API fails, or pagination stops advancing;
- the last fetched page is sliced locally to exactly 500 normalized rows.

For the configured 500 ceiling, a full result uses:

```text
page 1: limit=200, offset=0
page 2: limit=200, offset=200
page 3: limit=200, offset=400
local slice: first 500 normalized listings
```

Current maximum live inventory before dedupe:

```text
ordinary US-only query:
  up to 500 auctions + 500 fixed-price

priority query across US/GB/DE:
  up to 1,500 auctions + 1,500 fixed-price
```

The eBay-specific cap does **not** change:

- sold-comp HTML max/results and pacing;
- targeted sold-comp `targeted_comp_max_results: 60`;
- misspelling variants, which still request 20 per variant/lane;
- the 40-row default passed to Goldin, Pristine, Heritage, Yahoo, Fanatics,
  and ALT.

Official eBay constraints checked during implementation:

```text
Browse search page limit: 200
maximum result set: 10,000
maximum offset: 9,999
default Browse API application quota: 5,000 calls/day
```

Live validation:

```text
query: Disney Chrome 2023
marketplace: EBAY_US only
lane: AUCTION
requested: 500
returned: 500 normalized live auctions
```

This proved the connector moved past both the scanner's old 40-row ceiling and
eBay's 200-item first page.

Tradeoff: broader collection can materially increase raw rows, relevance work,
valuation time, PriceCharting enrichment, memory, and workbook/research size.
It also increases Browse API calls whenever a result set exceeds 200. The
latest 19m47s run occurred before the 500 cap, so the next full run may be
substantially longer. Do not confuse slower completion with a hang; monitor
`logs/scan.log` for advancing queries and valuation messages.

### Regression coverage after eBay pagination

New tests:

```text
TestConnectorCapabilitiesAndDedupe
  test_site_specific_result_cap_keeps_ebay_deep

TestEbayBrowsePagination
  test_follows_next_pages_past_the_200_item_page_limit
```

The pagination test verifies:

```text
offsets: 0, 200, 400
request limits: 200, 200, 200
returned normalized rows: 450 in the unit fixture
```

Final complete suite:

```text
Ran 142 tests in approximately 1.2 seconds
OK
```

The end-to-end synthetic demo also completed successfully with no network
calls.

### Current files changed and Git state

At approximately 13:05 CDT on 2026-07-26:

```text
 M code/main.py
 M code/models.py
 M code/scrapers/__init__.py
 M code/scrapers/base.py
 M code/scrapers/ebay.py
 M code/scrapers/fanatics_collect.py
 M code/scrapers/goldin.py
 M code/scrapers/heritage.py
 M code/scrapers/point130.py
 M code/scrapers/yahoo_jp.py
 M code/source_health.py
 M code/test_fixes.py
 M code/valuation/price_guide.py
 M docs/FEATURES.md
 M docs/README.md
 M secrets.example.yaml
 M sol extra high handoff.md
?? code/scrapers/alt.py
?? code/scrapers/authorized_feed.py
?? code/scrapers/pristine.py
?? docs/PLATFORM_ACCESS.md
```

`config.yaml` is ignored and therefore does not appear in `git status`, but it
contains important live changes including:

- enabled Pristine;
- Goldin/Pristine landed-cost settings;
- PriceCharting and eBay sold pacing;
- `max_results_per_query_by_site.ebay: 500`.

Never print or copy credential values from `config.yaml` or `secrets.yaml`
into chat, documentation, tests, or commits. This handoff deliberately records
configuration behavior without recording secrets.

No Git commit was made after the platform expansion, corrected run, crash fix,
or eBay cap work. The working tree contains interdependent, tested changes.
Do not use `git reset`, `git checkout --`, or selective restoration. If the
user asks to commit, inspect `git diff`, include the new source files, verify
ignored live config separately, rerun the full suite, and use a clear commit
message covering request control, platform expansion, trust gates, and eBay
pagination.

### Exact fresh-instance startup checklist

The next GUI instance should begin with:

```bash
cd "/Users/alekas/Desktop/ebay opportunities"
git status --short
.venv/bin/python -m unittest discover -s code -p "test_*.py"
tail -100 logs/scan.log
```

Expected test result:

```text
Ran 142 tests
OK
```

Then read, in this order:

```text
solextrahighhandoff.md
code/main.py
code/scrapers/ebay.py
code/valuation/price_guide.py
code/quality.py
code/report.py
code/test_fixes.py
config.yaml structure only; do not expose credentials
```

If the user is switching to a new goal, do not automatically launch another
full scan. First understand the new direction. If returning to scanner
quality, begin with exact identity/product-class gates, not source expansion
or looser thresholds.

### One-sentence state summary

The scanner now collects substantial live inventory across a deeply paginated
eBay lane plus healthy Goldin and partially healthy Pristine, produces a
complete auditable workbook, and protects many obviously risky rows, but its
Action-quality bottleneck is overly broad object/card identity in valuation;
all current changes are uncommitted, all 142 tests pass, and the next safe
engineering move is exact product-class and parallel/reference matching.
