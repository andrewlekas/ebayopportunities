# Card Arbitrage Scanner — Feature Overview

## Data collection
- **eBay (official API)**: auctions + Buy It Now/Best Offer across US/UK/DE
  marketplaces, FX-converted to USD; seller feedback, listing age, end times
- **Yahoo Auctions Japan**: auto-translated queries (query_ja override per
  watchlist entry), Buyee purchase links, full configurable landed-cost
  estimate (domestic + international shipping, proxy fee, insurance,
  import duty and FX spread)
- **Goldin**: live lots service with current bid/end time, 22%/$19 buyer
  premium, $6/$19 card shipping tiers, and 0.9% insurance on price realized
- **Pristine Auction**: public live search with current bid/end timestamp,
  canonical lot links, images, and 17% buyer premium
- **Fanatics Collect + ALT**: permission-gated API/local-export connectors
  for both auction and fixed-price inventory; no browser-key extraction
- **Connector capabilities**: full/BIN modes discover every source that
  advertises the requested lane instead of hard-wiring fixed price to eBay
- **Global cross-query dedupe**: native listing identity plus trusted physical
  asset/certificate IDs; one physical listing is valued once under its most
  specific query while retaining every query that found it. Titles alone
  never collapse distinct copies
- **Sold comps chain**: 130point (includes hidden Best Offer amounts) →
  eBay sold pages (auto-resume after bot-block cooldown) → cached history
- **PriceCharting API**: graded-card guide values (Pokemon, sports, games)
- **Misspelling hunter**: priority cards also searched under typo variants
  ("charzard", "1st addition") to find listings other buyers never see
- Politeness delays, retries, per-channel circuit breakers

## Valuation engine
- Fair value = sold comps (fuzzy title match, MAD outlier trim,
  recency-weighted median) blended with guide price; ask-based floor when
  neither exists (clearly flagged, low confidence)
- Grade normalization: **non-PSA graders count 1 point lower than PSA**;
  wrong grades excluded from matches and comps
- Foreign-language version filter (German/French/Spanish/Italian/Japanese
  incl. native names: Glurak, Dracaufeu, etc.)
- Auction expected close modeled from time remaining; BIN capture decays
  with listing age (fresh underpriced BIN = the prize)
- Opportunity Score = ROI × Confidence × Capture (capped ROI so
  too-good-to-be-true can't top the board)
- One landed-cost equation drives live EV and Excel bid ceilings; watchlist
  entries can select an exit marketplace with `resale_channel`
- PSA Vault economics are restricted to eligible graded Pokemon/sports cards;
  watches, parts, raw cards, games, comics, and memorabilia retain ordinary
  checkout tax and resale fees
- Scam defense: exclude keywords (reprint/proxy/pick-a-card/...), price
  <35% of market flag, low seller feedback penalty
- Self-improving loop: predictions vs actual closes logged every scan;
  learned settle ratio at 20+ matched closes; gradient-boosted close model
  at 150+ (deployed only if it beats the simpler model in cross-validation)

## Operations
- Cron: daily 6pm full scan → "Opp Runs"; every-30-min BIN sweep → "BIN runs"
- Telegram alerts: edge ≥ $150, ROI ≥ 15%, capture ≥ 50%, confidence floor;
  per-listing dedupe; failed sends retry
- Excel: Action tab (decide-now items), category tabs (Pokemon/Sports/
  Watches/Games/Pop Culture), persistent Trade Blotter snapshot, card-only
  Crossover, Source Health,
  Discovery quarantine, Movers (trusted 30d trends),
  human timing ("ends 2h 59m", amber <6h), hyperlinked titles, hidden
  audit column group (M–P)
- SQLite history.db: comp cache (48h), trusted fair-value trend lines,
  calibration data, persistent source readiness, 7-day guide-price cache
- **Persistent trade blotter**: the strongest tradeable rows are upserted to
  a private CSV without overwriting human status/notes/cash flows; landed
  cost, realized P&L/ROI, and holding time are derived on every run
- **Manifest-driven sources**: authorized JSON/CSV marketplace feeds declare
  field maps, auction/fixed capabilities, and buyer economics in YAML; an
  enabled manifest automatically joins scans and Source Health

## Config quick reference (config.yaml)
- `watchlist:` queries; `priority` auto-set by grade; `discovery: true` for
  broad themes; `query_ja` for Yahoo JP
- `filters:` min_value / max_price / exclude_keywords / min_listing_match
- `alerts:` thresholds + Telegram credentials
- `algorithm:` settle ratio, fees, grader shift/premiums, capture half-lives
- `scraping:` delays, circuit breaker, html comps pause/resume, misspell
- **Central trust gates**: disputed, suspicious, mixed-pool, ask-based,
  discovery, regraded, thin-comp and collection-rejected valuations cannot
  contaminate learning/history; decision outputs share the same tradeability
  policy while category tabs retain questionable rows for research.
- **Exact-card comp routing**: broad searches discover inventory, then
  cache-first, rate-capped sold searches price numbered cards using their
  card number and listing grade instead of a set-wide median.
- **Historical quarantine**: legacy fair-history rows are preserved with
  `trusted=NULL` and backed up during migration, but cannot drive Movers or
  portfolio marks. New rows pass the central evidence gate first.
- **Crossover allowlist**: only configured card categories and graders
  (default CGC/BGS/SGC/BVG Pokemon and sports cards) can enter.
- **Source readiness**: each run persists success/failure/breaker counts,
  deliberately disabled sources and comp-cache freshness, then displays
  them in the workbook.
- `trade_blotter:` controls the persistent CSV and per-run auto-capture count.
- `source_manifests/*.yaml` onboards additional authorized sources without a
  Python edit; raw secrets are rejected from manifests.

## Backlog
Obtain approved Fanatics/ALT data access, then onboard them through manifests
or their built-in authorized connectors. Add Mercari JP and expand
exit-channel support to consignors such as Probstein/DC Sports/COMC.
