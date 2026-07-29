# Collectibles Opportunity Scanner — Master Handoff

**Prepared:** 2026-07-28, America/Chicago
**Project owner:** Andrew Lekas
**Project root:** `/Users/alekas/Desktop/ebay opportunities`
**Intended reader:** a fresh Codex/ChatGPT instance, Claude, or another senior
coding system taking over the project with no conversational context
**Source-control branch:** `main`
**Remote:** `origin` → `https://github.com/andrewlekas/ebayopportunities`
**Parent commit before the work documented here:** `5fdc48a`
(`feat: harden opportunity pipeline and add source workflows`, 2026-07-26)
**Final commit:** this handoff is being committed together with the July 28
sports/set-needs/alert changes. A Git commit cannot contain its own hash, so
run `git log -1 --oneline --decorate` for the authoritative final hash.

---

## 0. Read this first

This is the current, consolidated handoff. It supersedes stale state,
thresholds, test counts, Git status, and “next task” statements in:

- `HANDOFF.md`
- `fable_handoff.md`
- `sol extra high handoff.md`
- `solextrahighhandoff.md`
- `opus_high_handoff.md`

Those files remain valuable historical records. They explain why many safety
rules exist, show the sequence of earlier failures, and contain detailed
live-run evidence. When they disagree with this document about the current
code or configuration, **this document wins**.

Do not expose credentials. The live `config.yaml`, `secrets.yaml`, `.env`,
local exports, databases, reports, downloaded guide CSVs, portfolio, and trade
blotter are intentionally ignored by Git. Read configuration structure when
needed, but never print API keys, OAuth secrets, Telegram tokens/chat IDs, or
authorized-feed credentials into chat, documentation, tests, or commits.

The most important current distinction is:

> The last production workbook was generated **before** the July 28 sports
> overhaul documented in sections 15–23. The new work passes 311 offline
> tests and a synthetic end-to-end run, but it has not yet been accepted by a
> fresh live scan.

Do not claim the sports problem is solved until the next live workbook proves
that Jordan, LeBron, Kobe, and exact sports targets survive the real funnel
without unrelated Tiger Woods objects or wrong-guide values.

---

## 1. Executive state in one page

### What the system is

This is not merely an eBay scraper. It is a multi-venue collectibles
acquisition and valuation pipeline that:

1. searches live listings and auctions;
2. gathers or reuses sold-comparable evidence;
3. resolves listings to exact collectible identities;
4. obtains guide values from PriceCharting/SportsCardsPro or local guide CSVs;
5. models landed acquisition cost and exit-channel net proceeds;
6. estimates fair value, edge, ROI, capture, confidence, and bid limits;
7. blocks untrusted/broad/mismatched evidence from purchase decisions;
8. writes one Excel workbook per run;
9. sends only qualified phone/Telegram alerts;
10. tracks decisions, closes, source health, and eventual learning data.

Andrew primarily transacts on eBay. eBay live-inventory recall matters, but
precision matters more at the final decision boundary: the scanner should
search broadly, then require exact evidence before suggesting a bid or BIN
purchase.

### Current headline state

- Branch is `main`.
- Before this handoff request, local and remote both pointed to `5fdc48a`.
- The July 28 code changes are intended to be committed and pushed with this
  file.
- Live configuration has 90 ordinary watchlist entries, 42 grails, one
  personal set need, and 12 generated structured sports targets: **102 scan
  entries total**.
- General fair-value floor is now **$500**, down from $1,000.
- Video Games remain at **$250**.
- The first set need is
  `1999 1st Edition Pokemon Trader PSA 9`, with a **$0 value-floor override**.
- Set needs bypass the general fair-value floor, but do **not** bypass
  positive-EV, evidence, dispute, identity, or tradeability requirements.
- Negative-EV fixed-price listings are now prohibited from Telegram, including
  grails and set needs.
- Static guide CSV refresh is now **168 hours / one week**.
- Older static CSVs remain usable; age only controls whether the download
  helper offers to replace them.
- Sports Cards are now routed only to SportsCardsPro data, not the generic
  PriceCharting host.
- Sports memorabilia is a separate category and no longer inherits sports-card
  guide values.
- Specific discovery listings may be promoted to decision candidates only
  after exact identity evidence resolves.
- A new `Sports Coverage` worksheet explains the sports funnel per query.
- Complete regression result: **311 tests pass**.
- Synthetic end-to-end result: **15 valued → 8 kept + 7 research**, workbook
  written, no outward API calls.

### Current live-source state

- **eBay Browse API:** generally healthy and the principal live source.
- **eBay sold HTML:** frequently challenged; it is separately paced and
  circuit-broken. Never describe this as the eBay API failing.
- **Goldin:** working and parsing live inventory.
- **Pristine:** working substantially, with conservative configured shipping.
- **Heritage:** often 403s and enters cooldown.
- **Yahoo Japan/Buyee:** transport succeeds but current parser returns no
  normalized listings.
- **Fanatics Collect:** connector exists, but disabled until authorized access
  or a normalized export is configured.
- **ALT:** connector exists, but disabled until authorized access or a
  normalized export is configured.
- **130point:** disabled.
- **PriceCharting:** paid token is configured, paced, cached, and budgeted.
- **SportsCardsPro:** same integration family and token path; used for Sports
  Cards. No local SportsCardsPro CSV is presently installed.
- **PokemonTCG.io:** no configured key, so correctly silent.

### Current data state

At handoff creation:

```text
database/history.db size:          approximately 116 MB
alerts:                            59
closed rows:                       217
sold comps:                        6,951
trusted/recorded fair history:     6,554
guide_cache values:                0
guide_product_cache products:      8,113
observations:                      19,544
source_health rows:                272
```

The empty `guide_cache` is expected after the July 28 guide-rule version bump.
The product cache remains populated and reusable. Do not delete the product
cache just because value-cache rows were invalidated.

Installed local guide files:

```text
pricecharting--other-cards.csv
pricecharting--pokemon-cards.csv
pricecharting--video-games.csv
```

Together they index roughly 289,000 products. There is currently no
`sportscardspro--*.csv`, so sports guide misses fall back to the paid
SportsCardsPro API and/or exact sold comps.

---

## 2. Andrew’s goal and working preferences

Andrew wants a practical acquisition tool for buying collectibles below fair
value across eBay and other auction/marketplace platforms. The one-file Excel
output is the central decision artifact. The core collecting scope includes:

- vintage and premium Pokémon;
- sports cards;
- selected sports memorabilia;
- sealed or professionally graded video games;
- watches as research inventory, with stricter caution;
- grails and personal set-completion needs;
- selected comics, Disney, and other collectibles.

Important preferences:

- eBay is the primary venue and should not be artificially shallow.
- False positives are worse than a quiet Action sheet.
- Broad searches are welcome for discovery, but broad values are not bid
  targets.
- The report should explain where rows disappeared rather than silently hiding
  them.
- Telegram should be scarce and trustworthy.
- A freshly available wanted item may be worth knowing about, but a
  fixed-price listing above fair value is never a purchase alert.
- Static guide data need not refresh daily. Weekly is sufficient.
- Personal set needs should be searchable even below the general portfolio
  value floor.
- “Commit to Git” means both a local commit and a push to the configured
  remote.
- Andrew commonly launches scans by double-clicking `.command` files.
- He may leave the computer while work proceeds; do not require unnecessary
  interactive supervision.

---

## 3. Source hierarchy and historical handoffs

Use this order when reconstructing context:

1. `CODEX_MASTER_HANDOFF_2026-07-28.md` — current state and latest changes.
2. `opus_high_handoff.md` — detailed July 26 architecture, fair-value
   rework, source workflows, trade blotter, and manifest onboarding.
3. `solextrahighhandoff.md` — very detailed history through the July 26 live
   run, marketplace expansion, report evidence, and eBay pagination.
4. `sol extra high handoff.md` — historical twin of the previous file.
5. `HANDOFF.md` — earlier consolidated architecture and build history.
6. `fable_handoff.md` — early reliability, breaker, pacing, and anti-spam
   work.

The two “sol extra high” files were once identical hard-linked/copies and
contain a great deal of duplicated historical text. Do not update both unless
Andrew explicitly requests it. This new master file exists to avoid continuing
that duplication.

---

## 4. Repository and runtime map

### Root helpers

```text
Run Scan.command
    Installs/updates the virtual environment, runs the complete scan, writes a
    dated workbook under reports/Opp Runs, and opens it for interactive runs.

Run BIN Sweep.command
    Runs fixed-price-oriented mode and writes under reports/BIN runs.

Run Tests.command
    Checks required configuration, reports learner state, runs all unit tests,
    and runs a synthetic no-network end-to-end demo.

Stop Scan.command
    Safely stops the active scan using the lock/PID conventions.

Reset Cooldowns.command
    Displays persistent breaker state and can clear selected local cooldowns.
    Clearing a local timer does not defeat an external block.

Download Price Guides.command
    Downloads missing/stale guide CSVs with the provider’s ten-minute
    inter-download pacing. The current freshness target is one week.

Check Price CSVs.command
    Reports local guide CSV coverage without network calls.

Check PriceCharting.command
    Probes exact guide resolution for representative collectibles.

Check Sports Card Prices.command
    Probes SportsCardsPro routing and token acceptance.

Check PriceCharting Coverage.command
    Compares guide coverage across categories.

Open Trade Blotter.command
    Opens/initializes the private persistent trade blotter.

Onboard Source.command
    Validates and installs a source manifest.

Check Source Feeds.command
    Lists and validates manifest-driven sources without unauthorized calls.

Set BIN Schedule.command
    Safely edits only the scanner’s scheduling lines, with backup and
    confirmation.
```

### Core Python files

```text
code/main.py
    Main orchestration, collection plans, relevance gates, global dedupe,
    targeted comp planning, valuation, classification, diagnostics, report,
    persistence, alerts, digest, and close tracking.

code/models.py
    Listing, SoldComp, Valuation, Opportunity and related dataclasses.

code/targets.py
    NEW July 28. Structured sports target expansion, set-need normalization,
    scan-entry merge/order, and strict returned-title validation.

code/valuation/identity.py
    Object/card/game/watch identity extraction, specificity, fingerprints,
    conflicts, guide queries, candidate scoring, object class, genre class.

code/valuation/comps.py
    Grade/year/card-number parsing, comp filtering, robust comp values,
    identity and variant conflict guards.

code/valuation/price_guide.py
    Local CSV and paid PriceCharting/SportsCardsPro resolution, product/value
    caches, candidate selection, grade routing, category budgets.

code/valuation/guide_csv.py
    Local CSV loader/index, file signature caching, row normalization, and
    guide-host provenance.

code/valuation/engine.py
    Fair value, guide/comps authority, identity trust, discovery promotion,
    landed economics, expected value, capture, confidence, Max Bid inputs.

code/economics.py
    Exit-channel economics, PSA Vault eligibility, landed cost, buyer
    premiums, sale proceeds, channel optimization.

code/quality.py
    Unified trust/tradeability blockers used by workbook, alerts, learner,
    history, digest, portfolio, and blotter.

code/report.py
    Excel workbook creation, category tabs, Today/Action, Set Needs,
    Sports Coverage, diagnostics, About, styles, sheet ordering.

code/alerts.py
    Profit/grail alert selection and Telegram/macOS delivery.

code/digest.py
    Scheduled/top-opportunity summaries.

code/db.py
    SQLite schema and persistence.

code/learner.py
    Close-model training with trusted evidence and per-auction safeguards.

code/closer.py
    Auction close settlement/retry.

code/trade_blotter.py
    Private CSV-backed decision and realized-P&L workflow.

code/source_registry.py
    Built-in and manifest source registration/capabilities.

code/source_onboarding.py
    Manifest validation/install/list workflow.

code/source_health.py
    Persisted source readiness and run-health summaries.
```

### Built-in source adapters

```text
code/scrapers/ebay.py
code/scrapers/goldin.py
code/scrapers/heritage.py
code/scrapers/yahoo_jp.py
code/scrapers/pristine.py
code/scrapers/fanatics_collect.py
code/scrapers/alt.py
code/scrapers/authorized_feed.py
code/scrapers/manifest_feed.py
code/scrapers/point130.py
code/scrapers/base.py
```

### Private/runtime directories

```text
database/       history.db and runtime backups
state/          locks, persistent breaker state, schedule backups
logs/           rotating scan logs
reports/        dated full-scan and BIN-sweep workbooks
guide_csv/      downloaded PriceCharting/SportsCardsPro data
trade_blotter/  private canonical workflow CSV
portfolio/      private portfolio CSV
imports/        authorized/local source exports
model/          learned parameters/model artifacts
test results/   self-check output
```

Most runtime contents are ignored by Git. Do not force-add them.

---

## 5. How to run and verify

From Terminal:

```bash
cd "/Users/alekas/Desktop/ebay opportunities"

# Full production scan
./Run\ Scan.command

# Fixed-price sweep
./Run\ BIN\ Sweep.command

# Direct full scan
.venv/bin/python code/main.py -o \
  "reports/Opp Runs/opportunities_$(date +%Y-%m-%d_%H.%M).xlsx"

# Unit tests
PYTHONPYCACHEPREFIX=/tmp/ebay-opportunities-pycache \
  .venv/bin/python -m unittest discover -s code -p "test_*.py"

# Synthetic end-to-end run, no network
.venv/bin/python code/main.py --demo -o /tmp/scanner_selfcheck.xlsx

# Full project self-check
./Run\ Tests.command
```

Expected current regression result:

```text
Ran 311 tests
OK
```

Expected current synthetic result:

```text
15 valued
8 kept
7 Research/Filtered explanations
no outward API calls
exit 0
```

The macOS system `python3` does not necessarily have `PyYAML` or
`BeautifulSoup`. Use `.venv/bin/python`. A failure importing `yaml` or `bs4`
under `/usr/bin/python3` is an environment mistake, not a product regression.

The local Python build emits an `urllib3` LibreSSL/OpenSSL warning. It is
currently non-fatal and unrelated to scanner logic.

---

## 6. End-to-end architecture

The run pipeline is:

```text
load config + external secrets
        |
        v
expand set needs + structured sports targets + ordinary watchlist
        |
        v
build per-source/per-lane query plans
        |
        v
collect live listings + cached/fresh sold comps
        |
        v
apply relevance, object, grade, variant, age, horizon, and price guards
        |
        v
global cross-query physical-listing dedupe
        |
        v
plan exact targeted sold-comp queries
        |
        v
identity resolution + guide/comp valuation
        |
        v
landed cost + exit optimization + edge/EV/ROI/capture/confidence
        |
        v
central evidence/tradeability gates
        |
        +--> trusted history / observations / learner / close tracking
        |
        +--> classify into kept versus Research-Filtered
        |
        +--> Today / Action / category / Set Needs / Grails / diagnostics
        |
        +--> trade blotter synchronization
        |
        +--> alerts and digest
```

The design principle is progressive trust:

- collection should favor recall;
- relevance should reject obvious mismatches;
- valuation should refuse incompatible identity evidence;
- Action/Today/Telegram should require exact/trusted economics;
- learning should accept an even narrower trusted subset.

---

## 7. Listing and valuation models

### `Listing`

Important fields include:

- source/site;
- native listing ID and canonical URL;
- title and originating query;
- all `matched_queries` after cross-query dedupe;
- listing type: auction, fixed, or hybrid;
- item price, shipping, buyer premium, tax and other landed components;
- bid count, created time, ending time, image;
- seller and source metadata;
- category;
- priority;
- grail label and score;
- discovery flag;
- selected resale channel;
- trusted asset/certificate identity where available.

July 28 fields:

- `promoted_from_discovery`: exact evidence promoted a once-broad discovery
  listing;
- `structured_target`: listing came through a strict structured sports target;
- `set_need`: listing is a personal set-completion need;
- `value_floor_override`: per-listing fair-value floor, including zero.

### `Valuation`

Important outputs include:

- fair value;
- comps value and guide value;
- evidence counts and age;
- guide product/match/score;
- identity key, identity match, and identity specificity;
- landed acquisition cost;
- expected close/current executable cost;
- selected exit channel;
- expected net proceeds;
- edge now;
- expected value;
- ROI;
- capture;
- velocity;
- confidence;
- decision score;
- Max Bid/Breakeven inputs;
- notes and hard-risk markers.

### `Opportunity`

An `Opportunity` is a `Listing` plus a `Valuation`. It does not imply the row
is actionable. The same model is used for trusted rows, browse-only discovery,
quarantined research, grails, and set needs. The central quality gate decides
which downstream consumers may act on it.

---

## 8. Identity system

The identity layer is the main defense against pricing the wrong collectible.

`CardIdentity`/`identity_of()` extracts:

- object class;
- year or season;
- subject/player/character;
- set/product tokens;
- card number, including alphanumeric numbers;
- parallel/color/finish;
- serial denominator;
- autograph state;
- relic/patch/jersey state;
- grader;
- printed numeric grade;
- effective grade after Andrew’s cross-grader rule;
- grade qualifiers such as Authentic/Altered;
- game platform/edition/condition where applicable;
- other discriminating variants.

Identity exposes:

- a stable fingerprint/grouping key;
- specificity from 0–1;
- candidate score;
- conflicts between two identities;
- a guide-search query;
- an object class.

Object classes include cards, games, comics, watches, watch parts, packs,
wrappers, coupons, figures, memorabilia, and other special cases. Different
physical object classes must not share a decision-grade pool.

Important identity rules:

- A card grader name does not automatically make an object a card.
- `PSA/DNA signed photo` is memorabilia, not a card.
- `2003 Upper Deck Tiger Woods Jersey Card #12` remains a card because the
  title explicitly identifies a card product.
- A sports serial such as `17/75` is not automatically card number 17.
- A Pokémon set position such as `4/102` is card number 4 and is not a serial
  print run.
- `1999-00` means years 1999 and 2000, not 1999 and 1900.
- A generic guide genre of `Sports` is treated as a sports video game, not as
  evidence that the product is a sports card.
- Different serial denominators, parallels, sets, card numbers, subjects,
  grades, object classes, or product variants can hard-conflict.

---

## 9. Sold comps and targeted evidence

The scanner maintains cached eBay sold comps in SQLite. The eBay sold route is
HTML and therefore independent from the official live Browse API.

Current sold-search control:

- only one sold HTML lifecycle runs at a time;
- configured quiet gap is 10 seconds between sold searches;
- the lifecycle lock covers request, parse, challenge handling, and cooldown;
- bot challenges count across the run;
- after three challenges the lane backs off;
- persistent circuit-breaker state survives runs;
- stale cached comps may be reused when a fresh fetch is blocked;
- targeted exact comp searches are limited per run to control duration.

Comp hygiene rejects:

- wrong subject;
- wrong year;
- wrong grade/effective grade;
- wrong card number;
- wrong object class;
- conflicting variants/parallels;
- reprints, proxies, pick-your-card listings and excluded junk;
- duplicate URLs/native listing identities;
- incompatible languages/editions where required;
- extreme outliers under robust rules.

For a sufficiently specific live listing, `targeted_comp_query()` builds a
separate sold search for that exact identity. This prevents a broad watchlist
query from setting the final value.

Current targeted limits from live config:

```text
full scan targeted queries:    20
BIN scan targeted queries:      6
results per targeted query:    60
minimum exact comps:             3
```

Discovery promotion can now request targeted comps, but only for a specific
numbered and graded card at or above 80% identity specificity.

---

## 10. Guide architecture

### Hosts

The integration supports:

- `pricecharting.com` for Pokémon/TCG, games, comics and other supported
  non-sports categories;
- `sportscardspro.com` for sports cards.

Sports Cards now use **only** SportsCardsPro candidates. This corrects the
Tiger Woods failure mode where generic PriceCharting could return a PSP golf
video game with a plausible-looking price.

Sports Memorabilia uses no card-guide hosts. It must rely on appropriate sold
evidence and remains subject to identity/tradeability gates.

### Local CSV first, API fallback

The guide checks local CSV rows before making a paid API call. CSV hits:

- have no API cost;
- are held to the same identity, candidate-margin, genre and grade-routing
  safeguards as API results;
- carry `_guide-host` provenance inferred from the filename;
- are restricted by category so a PriceCharting file cannot price a Sports
  Card.

Sports candidate guards require, when stated:

- SportsCardsPro provenance;
- compatible card genre;
- exact card number;
- exact year;
- meaningful set-token overlap;
- no subject/object/parallel conflict under the normal scorer.

### Product and value caches

- Product search/product payloads are cached separately from grade-specific
  guide values.
- Product IDs are reusable across grades.
- Value cache version is now:
  `2026-07-28-sports-identity-v3`.
- The version bump invalidated old guide values so values computed under the
  earlier cross-category rules are not reused.
- `guide_product_cache` remains populated.

### Paid API pacing and budget

- Minimum effective request pace is at least one call per second.
- Live config uses 1.05 seconds.
- Total budget is 400 outward guide calls per run.
- Category limits/reserves are:

```text
Pokemon Cards:         170
Sports Cards:          180
Video Games:            30
Other:                  20
Sports Memorabilia:      0
Total:                  400
```

This prevents early Pokémon rows from consuming the entire run before exact
Jordan/LeBron/Kobe targets are priced. Cache and local CSV hits do not spend
the budget.

### Guide authority

Current value authority:

1. exact/strong guide identity may lead;
2. enough identity-matched sold comps may corroborate or pull guide value
   downward;
3. exact comps may carry a listing when guide resolution misses;
4. a broad/mixed fallback can be shown for research but is blocked from
   decisions.

Guide and comps agreeing on the wrong broad product is not trustworthy.
Identity resolution is required.

---

## 11. Fair value and economics

### Fair value

The engine distinguishes:

- guide-led exact valuation;
- exact/strong sold-comp valuation;
- blended/corroborated valuation;
- broad/mixed fallback;
- ask-based research;
- unresolved/no-value results.

Broad or mixed evidence receives explicit notes and confidence caps. Hard
markers flow through `quality.NOTE_BLOCKERS` so every downstream consumer
agrees about actionability.

### Auction interpretation

Current configuration uses `auction_pricing: live`.

For live auctions, edge represents the opportunity at the current executable
price, not a promise that the auction will close there. Max Bid is the
decision boundary. The close model still records a configured settle ratio of
0.92, but the learner is cold and not deployed.

Opening-bid safeguards remain:

- a zero-bid auction far from close is not treated as takeable;
- a zero-bid auction near close may be actionable;
- a hybrid auction uses its known BIN when appropriate;
- unknown future close prices are not disguised as guaranteed profit.

### Landed acquisition cost

Landed cost includes venue-specific:

- item price/current bid;
- shipping;
- buyer premium and minimum premium;
- insurance;
- sales tax;
- proxy/FX/import assumptions where applicable.

Current marketplace-cost snapshot:

```text
Goldin:
  minimum buyer fee:       $19
  shipping below $1,000:    $6
  shipping at/above $1,000: $19
  insurance:               0.9%

Heritage:
  buyer premium:            22%
  minimum buyer premium:    $29

Pristine:
  buyer premium:            17%
  shipping estimate:        $15
```

### Exit optimization

The engine selects the best eligible exit route by expected net proceeds.
Manual overrides are respected only if eligible.

PSA Vault is restricted to:

- eBay acquisition;
- Pokémon Cards or Sports Cards;
- actual object class `card`;
- professionally graded;
- PSA, BGS, CGC, SGC, or BVG;
- price at or above $500;
- other configured eligibility conditions.

Watches, raw cards, games, comics, figures, wrappers, and memorabilia cannot
receive PSA Vault tax/fee treatment.

### Bid levels

The workbook distinguishes:

- **Breakeven:** highest acquisition price producing zero profit.
- **Max Bid:** highest acquisition price that retains the configured target
  return, currently 15%.

Do not collapse these two numbers. Max Bid should be below Breakeven.

---

## 12. Unified trust and tradeability

`quality.py` is the central safety authority. A row with a hard blocker may
remain visible in category or research tabs, but cannot enter:

- Today;
- Action;
- profit alerts;
- decision digest;
- trusted fair-history;
- learning;
- trusted portfolio marks;
- automatic trade-blotter capture.

Important blockers include:

- identity unresolved;
- mixed pool;
- ask-based value;
- disputed value;
- suspicious listing;
- wrong object/variant;
- discovery query that was not promoted;
- stale/weak evidence beyond configured trust;
- outside collection standards;
- impossible economics/ROI;
- `BELOW DECISION FLOOR`.

The July 28 `BELOW DECISION FLOOR` marker enables browse/decision floors to
diverge later without accidentally treating a browse row as actionable.
Currently both general floors are $500, so the distinction is architectural
and ready for future tuning.

---

## 13. Collection standards and current floors

Current live configuration:

```text
general legacy floor:        $500
general browse floor:        $500
general decision floor:      $500
Video Games browse floor:    $250
Video Games decision floor:  $250
global listing price cap:    $100,000
ordinary max ROI:            200%
live-mode max ROI:           1,000%
Pokémon grade floor:         PSA-equivalent 5
```

Video Games must be sealed or professionally graded.

Pokémon collection rules and era/grade protections remain. Grails can bypass
some collection preferences for visibility, but not the new negative-EV BIN
Telegram invariant.

New/existing junk exclusions include:

- reprint/proxy/replica;
- custom/fan-made/orica/digital;
- pick-your-card and set-break junk;
- modern novelty/tribute formats;
- 23kt;
- facsimile;
- novelty card;
- mix-and-match;
- blow-up card;
- other configured global exclusions.

Set needs use per-entry floors and are not controlled by the ordinary $500
minimum. They still have to produce a positive executable economics result
and trusted identity/evidence before entering decisions or alerts.

---

## 14. July 26 baseline now in `5fdc48a`

Commit `5fdc48a` is the immediate parent of the July 28 work. It consolidated a
large July 25–26 feature set. A new instance should understand that all of the
following already exists and should not be rebuilt:

### Reliability and request control

- atomic per-source admission/rate lanes;
- three-failure circuit breakers;
- cross-run exponential cooldowns;
- separate eBay live, sold HTML, and close lanes;
- challenge detection and run-wide backoff;
- controlled PriceCharting pacing, caching, and retry behavior;
- no immediate retry storms on 429/challenge responses;
- run footer with duration/API summaries;
- safe lock and stop behavior.

### Exact identity and guide-led valuation

- object/card/game identity extraction;
- exact guide candidate scoring;
- PriceCharting plus SportsCardsPro host support;
- product/value cache separation;
- grade-specific guide routing;
- effective-grade cross-grader logic;
- identity collision canary;
- broad/mixed/ask-based trust quarantine.

### Marketplace work

- eBay pagination beyond the first 200 results;
- site-specific result ceilings;
- Goldin lot premiums and landed costs;
- Heritage buyer premiums;
- Pristine connector and economics;
- capability-based source orchestration;
- Fanatics and ALT authorized-feed connectors;
- manifest-driven local/authorized sources;
- conservative cross-platform physical-asset dedupe.

### Decision workflow

- one workbook per run;
- complete Filter Waterfall and Research-Filtered explanations;
- Source Health;
- Today and Action decision tabs;
- grails and discovery separation;
- persistent private trade blotter;
- realized P&L calculations;
- automatic exit optimization;
- PSA Vault eligibility gates;
- Max Bid versus Breakeven;
- central trust gate shared by all consumers.

### Security and source onboarding

- secrets externalization and redaction;
- credential-bearing `config.yaml` ignored;
- downloaded CSVs and private user data ignored;
- source manifests reject embedded credentials;
- normalized local JSON/CSV feeds;
- safe onboarding/check helper commands.

### eBay depth

The scraper code supports paginated Browse API retrieval in 200-row pages.
The live configuration was later tuned from the historical 500 ceiling to:

```text
full-scan eBay cap: 250 per marketplace, per query, per lane
BIN-sweep eBay cap: 100 per marketplace, per query, per lane
secondary source cap: 40
```

For a priority query across EBAY_US, EBAY_GB, and EBAY_DE, the theoretical
full-scan maximum is 750 auction rows plus 750 fixed-price rows before
relevance/dedupe. The code can be raised later if API quota and run time allow.

---

## 15. July 27–28 production evidence before the sports fix

### July 27 full scan

The 2026-07-27 20:44 run:

```text
raw listing hits:              26,572
relevant:                      12,025
valued:                        12,025
final kept:                       133
decision-tradeable:                49
Research-Filtered:             11,976
runtime:                       32m 14s

source raw:
  eBay:                        25,728
  Goldin:                         540
  Pristine:                       304

API/source operations:
  PriceCharting: 400 OK, then budget skips
  eBay API:      508 OK
  eBay HTML:     197 skipped while breaker open
  Goldin:         92 API + 92 parse OK
  Pristine:       86 HTML + 86 parse OK, 6 HTML failures
  Yahoo:          20 HTML OK, 20 parse failures
  Heritage:        3 failures, then 89 skips
  Telegram:        3 OK
```

Dominant final removal was the old $1,000 fair-value floor: 11,193 rows.

### July 28 full scan

The 2026-07-28 19:17 run produced:

```text
raw listing hits:              26,634
relevant:                      12,306
valued:                        12,306
final kept:                       154
decision-tradeable:                61
Research-Filtered:             12,245
runtime:                       33m 45s

source raw:
  eBay:                        25,717
  Goldin:                         571
  Pristine:                       346

API/source operations:
  PriceCharting: 399 OK, 1 failed, 9,703 skipped after budget/breaker
  eBay API:      508 OK
  eBay HTML:       3 OK, 194 skipped after bot-challenge breaker
  Goldin:         92 API + 92 parse OK
  Pristine:       92 HTML + 92 parse OK
  Yahoo:          20 HTML OK, 20 parse failures
  Heritage:        3 OK, 4 failures, 85 skips
  Telegram:        2 OK
```

The eBay sold lane saw three bot challenges early and backed off for 30
minutes. The official eBay Browse API remained healthy. The run reused stale
sold pools where available.

### Why Sports Cards were almost all Tiger Woods

The observed Sports Cards sheet was dominated by Tiger Woods for structural
reasons:

1. The sports watchlist had several broad Tiger Woods queries:
   - `Tiger Woods Authentic Stars`
   - `Tiger Woods UDA`
   - `Tiger Woods Rookie PSA`
   - `Tiger Woods US Open Flag`
2. Those searches returned hundreds of eBay fixed-price rows and many
   non-card objects.
3. Michael Jordan/LeBron/Kobe searches were often broad, not exact
   year/set/number/grade identity contracts.
4. Broad discovery was intentionally blocked from Action, but broad Tiger
   rows could still dominate the sports browsing/report pool.
5. Generic PriceCharting could return golf video-game products, including
   PSP entries, for Tiger Woods phrases.
6. Jersey/photo/flag/framed memorabilia was being routed through Sports Cards.
7. The global guide-call budget was consumed in scan order, reducing later
   sports resolution.
8. The $1,000 floor removed many legitimate mid-tier sports cards while a few
   wrong/broad high valuations survived.
9. Discovery listings could not previously graduate into exact-card targets
   even when their returned title revealed a number and grade.

The July 28 changes directly address each of these mechanisms.

---

## 16. July 28 change 1 — sports identity resolution

### Cross-century season parsing

Old behavior constructed the end of a short season range by reusing the
starting century:

```text
1999-00 -> 1999 and 1900
```

Current behavior computes the second year and rolls forward on suffix wrap:

```text
1999-00 -> 1999 and 2000
2003-04 -> 2003 and 2004
```

This matters for basketball/hockey sets where the season appears in the
product name.

### Sports serial versus TCG set number

Old generic slash parsing could treat:

```text
2003 Exquisite LeBron James 17/75
```

as card number 17. That is normally a serial-numbered copy, not the set card
number.

Current rule:

- slash numerator becomes a card number only with TCG/Pokémon context;
- sports serials do not become fictitious card numbers;
- an explicit `#LJ1` remains the card number while `17/75` remains the print
  run;
- Pokémon `4/102` remains card number 4;
- the same Pokémon `4/102` is not also recorded as serial /102.

### Memorabilia before grader classification

Authentication/grading brands can appear on non-card objects. Current object
classification checks clear memorabilia before using a card grader as card
evidence, unless true card-product language exists.

Examples:

```text
PSA/DNA signed photograph                     -> memorabilia
Tiger Woods tourney-worn shirt framed display -> memorabilia
2003 Upper Deck Tiger Woods Jersey Card #12   -> card
```

Memorabilia vocabulary now includes lithograph, blow-up card, custom framed,
photos, flags, pennants, jerseys, shirts, hats, gloves, helmets, bats, balls,
pucks, clubs, tickets, programs, magazines, cut signatures and worn items.

### Generic sports genre

A guide row with genre `Sports` now classifies as a video game unless other
card evidence proves otherwise. This blocks Tiger Woods PGA Tour for PSP from
pricing a Tiger Woods trading card.

---

## 17. July 28 change 2 — sports guide hardening

### Category-aware guide routing

`PriceGuide.quote()` now accepts an optional category.

Routing:

```text
Sports Cards         -> SportsCardsPro only
Pokemon Cards        -> non-SportsCardsPro/PriceCharting
Video Games          -> non-SportsCardsPro/PriceCharting
Other                -> non-SportsCardsPro/PriceCharting
Sports Memorabilia   -> no card guide host
```

Older one-argument test doubles/integrations remain compatible when a legacy
listing lacks category.

### Local CSV provenance

The CSV loader annotates each row from its filename:

```text
sportscardspro--*.csv -> sportscardspro
pricecharting--*.csv  -> pricecharting
```

Sports rows reject incompatible CSV provenance. An installed Pokémon/game
PriceCharting file cannot supply a sports-card value.

### Strict sports candidate guards

Before normal candidate scoring, Sports Card candidates are filtered on:

- SportsCardsPro origin when known;
- compatible genre/object;
- exact card number when the listing states one;
- exact year when stated;
- set-token overlap when stated.

The normal identity scorer then still handles subject, parallel, serial,
autograph, relic, grade and ambiguity.

### Category guide budgets

The 400-call total budget is partitioned by category as documented in section
10. This ensures early rows cannot starve exact sports targets.

### Cache invalidation

The guide-value cache version was bumped. Old values derived before sports
host/category controls are cleared. Product payloads remain cached.

---

## 18. July 28 change 3 — structured sports targets

New module: `code/targets.py`.

Broad query strings are useful for discovery but are poor identity contracts.
`sports_targets` lets the configuration define:

- player/subject;
- year;
- set;
- card number;
- one or more desired grades;
- parallel;
- serial denominator;
- autograph;
- relic/patch;
- priority;
- optional resale/buy settings.

Each grade expands into its own exact priority query. Current generated
queries:

```text
1986 Fleer Michael Jordan #57 PSA 8
1986 Fleer Michael Jordan #57 PSA 9

1984 Star Michael Jordan #101 PSA 7
1984 Star Michael Jordan #101 PSA 8
1984 Star Michael Jordan #101 PSA 9

2003 Topps Chrome LeBron James #111 PSA 9
2003 Topps Chrome LeBron James #111 PSA 10

2003 Exquisite LeBron James #78 /99 Auto Patch BGS 8.5
2003 Exquisite LeBron James #78 /99 Auto Patch BGS 9
2003 Exquisite LeBron James #78 /99 Auto Patch BGS 9.5

1996 Topps Chrome Kobe Bryant #138 Refractor PSA 8
1996 Topps Chrome Kobe Bryant #138 Refractor PSA 9
```

Structured returned-title validation happens before valuation. A listing is
rejected if it fails the target contract on:

- object class;
- year;
- subject;
- card number;
- set;
- grade;
- parallel;
- serial;
- autograph;
- relic.

This prevents “Jordan” or “LeBron” search noise from spending guide/valuation
budget.

Vague legacy sports searches remain discovery-only:

- `Michael Jordan 1986 Fleer Auto`
- `Michael Jordan 1984 Star`
- `LeBron James 2003 Exquisite`
- `LeBron James 2008 Chrome`

Structured targets are searched before ordinary watchlist entries and marked
priority. Case-insensitive merge prevents duplicate searches if an identical
query already exists in the ordinary watchlist.

Cross-query global dedupe preserves:

- structured-target status;
- set-need status;
- promotion status;
- the most permissive explicit value-floor override;
- priority/grail metadata;
- all matched queries.

---

## 19. July 28 change 4 — discovery promotion

Before this change, every listing found through a discovery query remained
browse-only even if the returned title revealed an exact numbered/graded
card.

Current promotion process:

1. Listing arrives from a discovery query.
2. Title identity must be a card.
3. It must state a card number.
4. It must state a numeric grade.
5. Identity specificity must be at least 80%.
6. The planner creates an exact guide/sold-comp identity query.
7. Exact guide evidence or at least three exact comps must resolve.
8. No hard blocker may remain:
   - no ask-based value;
   - no mixed pool;
   - no identity unresolved;
   - no suspicious marker;
   - no dispute.
9. The listing’s `discovery` flag becomes false.
10. `promoted_from_discovery` becomes true.
11. Notes record:
   `PROMOTED FROM DISCOVERY: exact identity evidence resolved this card`.

Low-specificity discovery remains browse-only and does not multiply paid
queries.

Exact targeted sold comps can now establish identity when the guide does not
land, provided at least three exact comps exist and no broad fallback was
used.

---

## 20. July 28 change 5 — browse floors, decision floors, and set needs

### Separate floors

The classifier now supports:

```yaml
filters:
  browse_min_value: ...
  decision_min_value: ...
  browse_min_value_by_category: ...
  decision_min_value_by_category: ...
```

Backward compatibility:

- if the new keys are absent, `min_value` and
  `min_value_by_category` continue to work.

Behavior:

- below browse floor → Research-Filtered at `Fair-value floor`;
- at/above browse but below decision floor → visible category/browse row with
  `BELOW DECISION FLOOR`, blocked from Action/Today/alerts;
- at/above decision floor → may proceed if every other trust/economics rule
  passes.

Current general browse and decision floors are both $500. The split is built
for later experimentation without weakening decisions accidentally.

### Set-needs list

New configuration section:

```yaml
set_needs:
  - query: 1999 1st Edition Pokemon Trader PSA 9
    min_value: 0
    priority: true
```

Set needs:

- are normalized into scan entries;
- run before structured targets and ordinary watchlist entries;
- are priority;
- are not discovery;
- may define a per-entry value floor;
- merge case-insensitively with an identical watchlist query;
- preserve their floor/status through cross-query dedupe;
- receive a `Set Needs` workbook tab when any survive.

The floor override affects both report classification and whether a guide call
could change the outcome. A low-value set need is not prematurely denied guide
resolution merely because the ordinary floor is $500.

Set needs do **not** bypass:

- exact identity;
- positive expected value;
- output economics;
- confidence/trust gates;
- dispute/suspicion blocks;
- negative-EV BIN Telegram block;
- other collection safety rules unless separately configured.

---

## 21. July 28 change 6 — memorabilia and sports diagnostics

### Sports Memorabilia category

`report._category(query, title)` is now title-aware while keeping its old
one-argument contract.

If a sports query returns a title classified as memorabilia, the listing is
routed to:

```text
Sports Memorabilia
```

instead of:

```text
Sports Cards
```

This affects:

- category tab placement;
- guide-host eligibility;
- collection and output checks;
- diagnostics;
- sports coverage reporting;
- Vault eligibility.

Sports Memorabilia is never PSA Vault eligible and has no SportsCardsPro
guide-call budget.

### Sports Coverage worksheet

Every production report can now include a `Sports Coverage` sheet with one
row per sports query and columns:

- player/subject;
- query;
- category;
- raw hits;
- relevant listings;
- valued listings;
- guide-resolved listings;
- exact-comp listings;
- promoted discovery listings;
- browse survivors;
- action survivors;
- ask-based values;
- top rejection reason.

This is the first place to inspect when Andrew asks:

- “Why is every sports row Tiger Woods?”
- “Why did Jordan disappear?”
- “Did LeBron return raw listings but fail relevance?”
- “Did SportsCardsPro resolve anything?”
- “Was everything below the floor?”
- “Did exact comp searches fail?”

Do not diagnose missing sports inventory from the final Sports Cards tab
alone. Use `Sports Coverage`, `Filter Waterfall`, `Research-Filtered`, Source
Health, and `logs/scan.log` together.

---

## 22. Negative-EV BIN Telegram invariant

### The observed bug

Grail alert logic intentionally bypassed profit thresholds so Andrew could
learn that a wanted card was newly available or ending. That made sense for
auctions, but it also allowed freshly listed fixed-price grails whose ask was
well above fair value to generate Telegram messages.

The July 28 example/history included a grail BIN around $5,000 against scanner
fair value around $1,070. It was a wanted-item availability notification, not a
profitable recommendation, but the phone message was understandably
misleading.

### Current invariant

For a fixed-price listing:

```text
expected_value < 0  => never send via Telegram
```

This applies regardless of:

- grail status;
- priority;
- set-need status;
- freshness;
- normal alert bypass;
- future caller behavior.

The guard exists twice:

1. in `send_alerts()` before persistence/delivery;
2. at `_send_telegram()` transport boundary.

The second defensive check protects against future alert paths accidentally
passing a negative-EV BIN.

Auctions may still produce grail availability/endgame alerts under their
separate policy because the current bid is not necessarily the final price and
the alert can be useful before bidding. Normal Action/Max Bid economics still
control recommendations.

The digest already requires positive EV for ordinary opportunities, and grail
digest entries are auction-oriented.

---

## 23. Static price CSV freshness

The downloader previously treated files around 20 hours old as stale because
the upstream catalogs may regenerate daily. Andrew does not need daily static
guide replacement.

Current behavior:

- default `FRESH_HOURS = 168`;
- live config `guide_csv.fresh_hours = 168`;
- downloader reads the configured value;
- a CSV younger than 168 hours is skipped unless `--force`;
- older CSVs are still loaded and used by the pricer;
- age does not invalidate static guide rows;
- age only decides whether `Download Price Guides.command` offers to replace
  the file;
- provider’s documented one-download-per-ten-minutes pacing remains.

The command’s comments/output now explain this distinction.

`Check Price CSVs.command` and `code/check_guide_csv.py` now evaluate the full
configured scan-entry set, including structured targets and set needs, not
only the raw watchlist.

At implementation time:

```text
configured scan entries:       102
queries covered by local CSV:   23
```

The three installed PriceCharting CSVs do not cover SportsCardsPro targets.
Download sports CSVs only if the subscription permits them and while obeying
the ten-minute provider limit.

---

## 24. Current safe configuration snapshot

This records behavior, never credentials.

```yaml
sites:
  - ebay
  - yahoo_jp
  - goldin
  - heritage
  - pristine

marketplaces:
  - EBAY_US
  - EBAY_GB
  - EBAY_DE

watchlist_count: 90
grail_count: 42
expanded_scan_entry_count: 102
structured_sports_query_count: 12
set_need_count: 1

scraping:
  request_delay_seconds: 3.5
  ebay_sold_request_delay_seconds: 10
  parallel_queries: 10
  html_timeout_seconds: 12
  challenge_backoff_after: 3
  max_results_per_query: 40
  max_results_per_query_by_site:
    ebay: 250
  max_results_per_query_by_site_bin:
    ebay: 100
  targeted_comp_queries_per_run: 20
  targeted_comp_queries_per_bin_run: 6
  targeted_comp_max_results: 60
  close_lookups_per_run: 20
  international_priority_only: true
  comps_warm_per_sweep: 6
  use_html_comps: true
  use_130point: false
  circuit_breaker_failures: 3
  breaker_cooldown_minutes: 30
  breaker_cooldown_max_hours: 24

pricecharting:
  request_delay_seconds: 1.05

guide_csv:
  fresh_hours: 168

algorithm:
  auction_pricing: live
  auction_settle_ratio: 0.92
  min_specific_comps: 3
  mixed_pool_confidence_cap: 0.25
  discovery_promotion_specificity_floor: 0.80
  guide_lookups_per_run: 400
  guide_lookups_per_run_by_category:
    Pokemon Cards: 170
    Sports Cards: 180
    Video Games: 30
    Other: 20
    Sports Memorabilia: 0
  psa_vault:
    enabled: true
    min_price: 500
    sell_fee_rate: 0.07
    eligible_categories: [Pokemon Cards, Sports Cards]
    requires_graded: true
    eligible_graders: [PSA, BGS, CGC, SGC, BVG]

filters:
  min_value: 500
  browse_min_value: 500
  decision_min_value: 500
  max_price: 100000
  max_roi: 2.0
  pokemon_grade_floor: 5
  video_games_sealed_or_graded: true
  min_value_by_category:
    Video Games: 250
  browse_min_value_by_category:
    Video Games: 250
  decision_min_value_by_category:
    Video Games: 250

output:
  min_expected_value: 0
  max_rows: 1000
  today:
    hours: 24
    fresh_hours: 24
    min_expected_value: 75
    min_confidence: 0.25
    max_bid_target_roi: 0.15

bin:
  priority_only: false
  freshness_half_life_hours: 24
  offer_target_ratio: 0.8

alerts:
  enabled: true
  priority_only: true
  min_edge_now: 150
  min_roi: 0.15
  max_roi: 2.0
  max_roi_live: 10.0
  min_confidence: 0.15
  min_capture: 0.5
  grails:
    enabled: true
    fresh_hours: 24
    ending_hours: 24
    max_per_run: 5
```

Credential-presence checks currently pass for eBay, PriceCharting and
Telegram. Do not record the values.

---

## 25. Current workbook contract

Sheet order is decision-first, research/diagnostics later:

```text
Today
Action
Trade Blotter
Set Needs                  when rows exist
Pokemon Cards              when rows exist
Sports Cards               when rows exist
Sports Memorabilia         when rows exist
Video Games                when rows exist
Discovery                  when rows exist
Watches                    when rows exist
Other                      when rows exist
Grails                     when rows exist
Crossover                  when rows exist
Portfolio                  when configured
Movers                     when rows exist
Sports Coverage            when rows exist
Source Health              when rows exist
Filter Waterfall
Research-Filtered
About
```

Important semantics:

- `Today`: deadlines/freshness subset of decision-grade rows.
- `Action`: tradeable positive-decision rows, excluding watches.
- `Trade Blotter`: read-only snapshot; edit the canonical CSV instead.
- `Set Needs`: all surviving configured set-need rows.
- category tabs: may include browse-visible rows not qualified for Action.
- `Discovery`: unresolved broad research only.
- `Grails`: wanted-item visibility, separately capped/grouped.
- `Sports Coverage`: sports funnel diagnostics.
- `Source Health`: network/parser/cache readiness.
- `Filter Waterfall`: count reconciliation.
- `Research-Filtered`: valued rows rejected or decision-quarantined, with
  Stage and Reason.
- `About`: column meanings and safety semantics.

Never populate empty decision sheets with rejected raw rows to make the report
look full.

---

## 26. Trade blotter

Canonical private file:

```text
trade_blotter/trade_blotter.csv
```

It is ignored by Git. The workbook tab is a snapshot, not the source of truth.

Each live scan:

- captures strongest eligible non-watch opportunities;
- upserts by trusted physical listing/asset identity;
- refreshes market fields;
- preserves human workflow and actual cash-flow fields;
- derives actual landed cost, realized profit, realized ROI and holding days;
- writes atomically;
- backs up before schema migration.

Typical user-editable status values:

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

Do not overwrite the blotter with workbook data. Do not commit it.

---

## 27. Source-by-source status and cautions

### eBay

Live listings use the official Browse API and OAuth. Pagination is implemented
in constant 200-item pages and sliced to the configured cap. Live API and sold
HTML have separate health.

Current configuration:

- full: 250 per marketplace/query/lane;
- BIN: 100 per marketplace/query/lane;
- priority queries may search US/GB/DE;
- ordinary international behavior is restricted.

Sold comps use HTML and are vulnerable to bot challenges. Current safeguards:

- serialized lifecycle;
- 10-second quiet gap;
- three challenges then backoff;
- persistent cooldown;
- stale cache fallback.

Do not repeatedly clear cooldowns to force a challenged page.

### Goldin

Current parser and API path were healthy in the latest full run:

```text
92 API operations OK
92 parse operations OK
571 raw listings
```

Use lot-provided premiums where present and the configured minimum/shipping/
insurance fallback. Verify non-card shipping and unusual lot-level costs
before acting.

### Pristine

Latest full run:

```text
92 HTML operations OK
92 parse operations OK
346 raw listings
```

Configured buyer fee is 17%, shipping estimate $15. Exact vintage searches
may return little while broader searches return many rows.

### Heritage

Frequently returns HTTP 403 and enters cooldown. Latest run had three parseable
responses, four failures, then 85 skips. Treat it as degraded, not healthy.

### Yahoo Japan/Buyee

Transport works, but the parser does not recognize current result markup:

```text
20 HTML operations OK
20 parse failures
```

This is a parser/schema task. Respect the source and do not substitute
unapproved scraping techniques.

### Fanatics Collect

Connector supports normalized authorized feeds. It remains disabled because
no approved API/export is configured. Do not invent an endpoint.

### ALT

Same authorized-feed policy as Fanatics. Connector exists but is disabled
until Andrew supplies access/export.

### 130point

Disabled. It is not required for the current pipeline.

### PriceCharting/SportsCardsPro

Paid API is paced and budgeted. Local CSV is preferred. Product payloads are
cached. Network timeout/404 failures must never cache a false definitive miss.

Sports Cards use SportsCardsPro only. There is no local sports CSV yet.

---

## 28. Database and learner

Primary database:

```text
database/history.db
```

Important tables:

- `comps`: sold-comparable cache;
- `observations`: scan snapshots with evidence metadata;
- `fair_history`: trusted value history;
- `closed`: tracked auction outcomes;
- `alerts`: alert dedupe/history;
- `guide_cache`: grade-specific guide values;
- `guide_product_cache`: reusable product payloads/IDs;
- `guide_meta`: cache-rule version;
- `source_health`: readiness/history.

Database connections must resolve through the configured project path. Do not
reintroduce a stray `history.db` in the repository root.

Learner safeguards:

- only trusted evidence;
- no discovery/mixed/ask-based/unresolved inputs;
- legacy rows without trust attestation quarantined;
- per-auction rather than per-snapshot weighting;
- GroupKFold by auction identity;
- minimum sample and accuracy deployment gates;
- cold-start parameters are always written;
- existence of a model file does not imply deployment.

Current learner state remains cold:

```text
0 / 20 trustworthy closes
ML deployed: false
```

Continue using configured auction behavior until enough clean closes exist.

---

## 29. Alerts and digest

### Profit alerts

Ordinary alerts require:

- enabled;
- priority when configured;
- tradeable/trusted row;
- minimum edge;
- minimum ROI;
- ROI sanity ceiling;
- minimum confidence;
- minimum capture;
- alert-dedupe freshness.

### Grail alerts

Grail alerts answer a different question: “Is a wanted object newly available
or entering the endgame?” Auctions can bypass ordinary profit gates under
configured freshness/end-time/cap rules.

Fixed-price grails can no longer bypass negative EV.

### Telegram

Transport uses breaker/request control and must never log the token or chat
ID. Negative-EV fixed-price rows are removed at selection and transport.

### Digest

Digest is separately configured. It summarizes top qualified opportunities
and grails. Do not let a digest path bypass central tradeability or
fixed-price negative-EV rules.

---

## 30. Git and security model

Tracked source is on `main`. Runtime/private files are ignored.

Do not:

- run destructive reset/checkout commands against user work;
- force-add `config.yaml`, `secrets.yaml`, `.env`, databases, reports, logs,
  guide CSVs, portfolio, private imports, or trade-blotter CSVs;
- embed credentials in source manifests;
- print tokens in diagnostics;
- commit `.DS_Store`, virtualenv files, caches, or generated workbooks.

Before any commit:

```bash
git status --short
git diff --check
git diff --stat
git diff
git ls-files --others --exclude-standard
```

Stage explicit intended paths. Inspect:

```bash
git diff --cached --check
git diff --cached --stat
git diff --cached
```

Then commit and push:

```bash
git commit -m "<clear message>"
git push origin main
```

Andrew’s phrase “commit to Git” means local and remote.

The live `config.yaml` is ignored. Operational changes to that file remain on
this Mac but will not travel in Git. Document behavior in the handoff without
copying credentials.

---

## 31. Files in the July 28 commit

### New

```text
CODEX_MASTER_HANDOFF_2026-07-28.md
code/targets.py
code/test_sports_pipeline.py
```

### Modified

```text
Download Price Guides.command
code/alerts.py
code/check_guide_csv.py
code/fetch_guide_csv.py
code/main.py
code/models.py
code/quality.py
code/report.py
code/test_fixes.py
code/valuation/comps.py
code/valuation/engine.py
code/valuation/guide_csv.py
code/valuation/identity.py
code/valuation/price_guide.py
```

### Ignored but materially updated locally

```text
config.yaml
```

Local config changes include:

- structured sports targets;
- first set need;
- general $500 floors;
- category guide budgets;
- 80% discovery-promotion floor;
- 168-hour CSV freshness;
- sports memorabilia guide budget zero;
- expanded junk exclusions;
- comments clarifying negative-EV BIN behavior.

---

## 32. Regression coverage added/updated

New July 28 tests cover:

- `1999-00` and `2003-04` cross-century season expansion;
- sports serial versus Pokémon set-number parsing;
- memorabilia versus jersey-card object classification;
- structured target expansion by grade;
- set-need/watchlist merge;
- strict wrong-year/wrong-number target rejection;
- SportsCardsPro-only host routing;
- rejection of a Tiger Woods PSP guide candidate;
- exact-evidence discovery promotion;
- $500 general floor;
- zero-floor set-need override;
- browse-versus-decision quarantine;
- negative-EV grail BIN blocked before Telegram;
- weekly static CSV refresh default.

Existing targeted-comp tests were updated:

- specific discovery can generate an exact comp query;
- vague discovery cannot;
- already-exact queries do not multiply.

The production `PriceGuide.quote(identity)` contract remains compatible with
older one-argument test doubles when category is absent.

Final checks performed before the commit:

```text
unit tests:                311, OK
synthetic full pipeline:   OK
synthetic workbook:        written successfully
network during demo:       none
git diff --check:          clean
```

The project self-check script ends with an interactive “Press Enter” prompt.
When run with stdin redirected from `/dev/null`, the shell process may return
1 at that final `read` even though its own output says:

```text
RESULT: everything passed.
```

Judge the test/demo result, not the deliberately interactive prompt’s EOF
status.

---

## 33. Known limitations after this commit

### 33.1 The sports overhaul needs a live acceptance run

Offline validation is strong, but no production workbook has yet exercised the
new exact targets, category guide budgets, memorabilia routing and Sports
Coverage sheet together.

Acceptance questions:

- Do all 12 structured sports queries run?
- Do raw/relevant/valued counts appear in Sports Coverage?
- Do Jordan, LeBron and Kobe rows survive where inventory exists?
- Are wrong years, grades, card numbers and memorabilia rejected early?
- Does any Tiger Woods PSP/game guide result land? It must not.
- Does SportsCardsPro receive sports queries and generic PriceCharting avoid
  them?
- Does the Sports Cards sheet contain actual cards?
- Does Sports Memorabilia separate shirts/photos/flags/jerseys?
- Do category budgets preserve sports calls late in the run?

### 33.2 No local SportsCardsPro CSV

Sports Cards still spend paid API calls when not cached. Consider downloading
eligible SportsCardsPro sets/catalogs, but do not violate subscription or
ten-minute download rules.

### 33.3 eBay sold HTML remains fragile

The pacing change lowers pressure but cannot make an HTML bot challenge
disappear. Cached comps and guide resolution remain essential. A future
approved sold-data source would improve this materially.

### 33.4 eBay Marketplace Insights remains a possible future application

Real sold prices through an official limited-release API would be preferable
to HTML. It may require partner enrollment, approval, mockups and
category-specific access. Do not treat it as available today.

### 33.5 Yahoo parser remains broken

Transport success with zero parsed rows is not useful. Repair requires
inspection of authorized/current markup and parser fixtures.

### 33.6 Heritage is unstable

403 behavior persists. Maintain breaker discipline.

### 33.7 Fanatics and ALT still need access

The code exists. The missing requirement is authorized data, not more
guesswork.

### 33.8 Watches remain research-heavy

PSA Vault misrouting is fixed, but watch reference/complication matching is
still less mature than cards. Watches are excluded from Action. Future work:

- reference-number extraction and family matching;
- component-versus-complete-watch gates;
- Travel Time/World Time/Chronograph/Day-Date separation;
- realistic watch exit routes and fees.

### 33.9 Static config is not versioned

`config.yaml` is intentionally ignored to protect credentials. Code clones do
not automatically receive Andrew’s operational watchlist, targets and floors.
This handoff is the safe behavioral record. A future improvement could split
public policy/config from private secrets, but do not casually track the
current file.

### 33.10 Category budgets are ceilings, not a dynamic optimizer

The new allocation prevents starvation but is static. After a live run, use
Sports Coverage and source health to tune 170/180/30/20 rather than guessing.

### 33.11 General browse and decision floors are currently equal

The dual-floor architecture is ready, but it does not yet create a
$500-browse/$1,000-decision distinction because Andrew requested the broader
minimum to become $500. Change these independently only with explicit
direction.

---

## 34. Exact next live-run checklist

### Before starting

```bash
cd "/Users/alekas/Desktop/ebay opportunities"
git status --short --branch
git log -3 --oneline --decorate
.venv/bin/python -m unittest discover -s code -p "test_*.py"
tail -80 logs/scan.log
ls -la state
```

Expected Git after this handoff request:

```text
main aligned with origin/main
working tree clean
latest commit contains sports targeting, set needs, alert safety and handoff
```

Do not clear an active sold-HTML cooldown merely to make the acceptance run
look cleaner.

### Run

Double-click:

```text
Run Scan.command
```

or:

```bash
.venv/bin/python code/main.py \
  -o "reports/Opp Runs/opportunities_$(date +%Y-%m-%d_%H.%M).xlsx"
```

### Watch the log

Look for:

- the Trader set need first;
- 12 structured Jordan/LeBron/Kobe queries;
- normal eBay Browse API progress;
- eBay sold challenges/cooldown reported separately;
- PriceCharting/SportsCardsPro category budget messages;
- no generic sports-game candidate accepted as card evidence;
- a normal valuation phase after collection;
- `blocked N negative-EV BIN(s) before Telegram` when applicable;
- workbook write and run footer.

### Inspect the workbook in this order

1. `Sports Coverage`
2. `Sports Cards`
3. `Sports Memorabilia`
4. `Set Needs`
5. `Today`
6. `Action`
7. `Research-Filtered`
8. `Filter Waterfall`
9. `Source Health`
10. `About`

### Record acceptance evidence

Capture:

- raw/relevant/valued/action counts for each structured player;
- top rejection reasons;
- guide-resolved and exact-comp counts;
- number of promoted discovery rows;
- whether Trader appears and at what fair/current price;
- whether any fixed negative-EV grail was suppressed;
- API-call totals and category-budget exhaustion;
- source health;
- full run time;
- any repeated identical values across different sports identities.

Only tune targets/floors after this evidence.

---

## 35. Recommended next engineering priorities

### Priority 1 — live acceptance and sports target tuning

Run once, inspect Sports Coverage, then adjust:

- target grades;
- exact sets/card numbers;
- category call budgets;
- broad discovery phrases;
- specific exclusions.

Do not add dozens of speculative targets before confirming the first 12 work.

### Priority 2 — build out the personal set-needs list

The mechanism now exists. Add needs in `config.yaml` with:

```yaml
- query: Exact year/set/card/grade phrase
  min_value: 0
  priority: true
```

Prefer exact card identity. If set needs eventually require different EV rules
or “notify even at fair value,” make that a separate explicit policy rather
than weakening the general action system.

### Priority 3 — local sports guide coverage

If Andrew’s subscription permits SportsCardsPro CSV exports:

- download the most relevant sports categories/sets;
- verify filenames preserve `sportscardspro--` provenance;
- run `Check Price CSVs.command`;
- confirm exact sports targets resolve locally;
- keep weekly refresh.

### Priority 4 — approved sold-data access

Investigate official eBay Marketplace Insights/partner access. This could
replace fragile HTML sold search and improve freshness.

### Priority 5 — repair Yahoo and stabilize secondary venues

- update Yahoo parser with fixtures;
- investigate Heritage authorized access/403 pattern;
- validate Pristine economics on actual candidate lots;
- connect Fanatics and ALT when Andrew supplies authorized feeds.

### Priority 6 — watch identity

Implement complete-watch versus component and reference-family matching before
allowing watches into Action.

### Priority 7 — configuration split

Consider:

- tracked non-secret policy/watchlist template;
- ignored secrets-only file;
- local overrides.

This would make targets/floors versionable without exposing credentials.

---

## 36. Historical bugs that must never return

This is a condensed regression-review list built from all prior handoffs.

1. Function-local `_category` import shadowing killed runs.
2. Zero-bid far-away auction treated as immediately buyable.
3. Hybrid opening bid used instead of known BIN.
4. Max Bid equaled Breakeven.
5. Vault fee stacked on ordinary exit fee.
6. Vault tax exemption applied without a Vault exit.
7. Watches/watch parts received PSA Vault treatment.
8. Raw cards inherited a graded query’s grade/value.
9. Cross-grader discount applied twice to guide values.
10. Impossible grades such as CGC 85 became enormous numeric grades.
11. Grade qualifiers were mistaken for numeric grades.
12. Seller adjectives were extracted as the subject.
13. Unicode/accent normalization broke names/Japanese.
14. Exact card queries accepted unnumbered/wrong-number comps.
15. Vintage cards used modern tribute comps.
16. Sports season `1999-00` became 1900.
17. Sports serial `17/75` became card #17.
18. Pokémon `4/102` became serial /102.
19. Different Disney parallels shared one broad value.
20. Tiger Woods PSP game priced a sports card.
21. Signed photo/jersey memorabilia classified as a card because of PSA/DNA.
22. Superman cards/comics/wrappers/coupons/figures shared one pool.
23. Watch dials/buckles/parts were priced as complete watches.
24. Excluded keywords screened listings but not comps.
25. Duplicate comp URLs counted multiple times.
26. Same physical listing appeared twice under broad and exact queries.
27. Cross-listed assets were collapsed on title alone.
28. Discovery/mixed/ask-based values entered decisions or learning.
29. Agreement between two broad sources was treated as exact evidence.
30. Weak legacy fair history drove portfolio marks.
31. Snapshot count, not auction count, dominated learner medians.
32. Random snapshot folds leaked auction identities into ML validation.
33. A stale learned model remained active after cold start.
34. Source Health advertised a source while runtime behavior disagreed.
35. Relative DB paths created a stray root history database.
36. Report generation padded empty sheets with rejected rows.
37. eBay live API health was conflated with sold-HTML bot challenges.
38. Parallel source workers bypassed atomic breaker admission.
39. Challenge retries spammed the same endpoint.
40. 429 responses retried immediately.
41. Network failure cached a permanent guide miss.
42. PriceCharting product and grade value caches were mixed.
43. Different guide hosts shared a cache key.
44. Broad early queries exhausted guide budget before sports targets.
45. BIN sweep outlived its schedule and collided with the next run.
46. Cron run opened workbooks or interactive run failed to open them.
47. Run exit 1 hid a workbook that had been written successfully.
48. Fanatics/ALT code guessed protected endpoints without authorization.
49. Source manifests allowed embedded secrets.
50. Fixed-price grails above fair value generated Telegram alerts.
51. Static guide CSVs were declared stale after roughly one day despite being
    perfectly usable.
52. A below-decision browse row could reach another downstream decision path.
53. Sports memorabilia appeared in Sports Cards and inherited card guides.
54. Structured exact targets accepted wrong year/number/grade returns.
55. Set-need dedupe lost its value-floor override.

Every material change should add or preserve a regression test tied to the
real failure.

---

## 37. Development conventions

- Work in the real Desktop project.
- Preserve unrelated user changes.
- Use targeted patches.
- Read before editing.
- Never reset or restore broad paths without explicit permission.
- Use `.venv/bin/python`.
- Use `rg` for search.
- Add a regression test for every bug.
- Prefer fixtures based on real bad rows.
- Test the test against the old behavior when practical.
- Run the full suite, not only a focused file.
- Run the synthetic demo before declaring the pipeline healthy.
- Use live network checks only when needed and respectful.
- Respect source-specific pacing and breakers.
- Never weaken central trust gates to make Action less empty.
- Do not lower floors merely to manufacture volume.
- Keep report diagnostics synchronized with filtering behavior.
- Any new decision consumer must call the same tradeability gate.
- Any new alert path must enforce the negative-EV BIN invariant.
- Any new guide source must carry provenance and category/object compatibility.
- Any new source must include landed economics and Source Health behavior.
- Update this handoff when architecture, config policy, source status, report
  contract, test count, or Git state materially changes.

---

## 38. Fresh-instance startup checklist

The next coding system should do this before changing anything:

```bash
cd "/Users/alekas/Desktop/ebay opportunities"

git status --short --branch
git log --oneline --decorate -10
git remote -v

sed -n '1,240p' CODEX_MASTER_HANDOFF_2026-07-28.md
rg -n '^## ' CODEX_MASTER_HANDOFF_2026-07-28.md

PYTHONPYCACHEPREFIX=/tmp/ebay-opportunities-pycache \
  .venv/bin/python -m unittest discover -s code -p "test_*.py"

tail -120 logs/scan.log
ls -lt "reports/Opp Runs" "reports/BIN runs" | head -25
ls -lh guide_csv
```

Then inspect, in order:

```text
config.yaml structure only; do not expose credentials
code/targets.py
code/main.py
code/valuation/identity.py
code/valuation/comps.py
code/valuation/price_guide.py
code/valuation/engine.py
code/quality.py
code/report.py
code/alerts.py
code/test_sports_pipeline.py
code/test_fixes.py
```

If the user asks to run production, perform the acceptance checklist in
section 34. If the user switches goals, do not automatically launch a
33-minute full scan.

---

## 39. “Picked up where we left off” test

A new instance is oriented only when it can state all of the following:

- This is a multi-source decision/evidence system, not just a scraper.
- eBay Browse API and eBay sold HTML are separate health lanes.
- The current general value floor is $500, games $250.
- Set needs may have a zero floor but still require positive EV and trust.
- The first need is `1999 1st Edition Pokemon Trader PSA 9`.
- There are 12 structured Jordan/LeBron/Kobe exact sports queries.
- Structured returns are validated before valuation.
- Specific discovery cards can be promoted only after exact evidence.
- Sports Cards use SportsCardsPro only.
- Sports memorabilia is a separate category.
- No local SportsCardsPro CSV is installed.
- Static CSV refresh is weekly, but older CSVs remain usable.
- Negative-EV BINs can never reach Telegram, even as grails.
- Sports Coverage is the first diagnostic for missing players.
- The latest production workbook predates these fixes.
- 311 tests and the synthetic demo pass.
- The next required evidence is one clean live acceptance scan.
- Yahoo parsing and Heritage stability remain unresolved.
- Fanatics and ALT need authorized access, not guessed scraping.
- Watches remain excluded from Action and need reference/component work.
- `config.yaml` is local/ignored because it contains credentials.
- “Commit” means local plus remote.

If the new instance cannot say these things, it has not finished reading.

---

## 40. One-sentence handoff

The scanner now has a tested exact sports-target/set-needs pipeline, category-
safe SportsCardsPro valuation, discovery promotion, $500 general floors,
weekly static guides, separate memorabilia diagnostics, and a hard prohibition
on negative-EV BIN Telegram alerts; the code is ready for the next live
acceptance run, whose job is to prove that Jordan/LeBron/Kobe coverage replaces
the old Tiger Woods-dominated sports output without weakening the system’s
evidence and economics gates.
