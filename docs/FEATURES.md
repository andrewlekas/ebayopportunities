# Card Arbitrage Scanner — Feature Overview

## Data collection
- **eBay (official API)**: auctions + Buy It Now/Best Offer across US/UK/DE
  marketplaces, FX-converted to USD; seller feedback, listing age, end times
- **Yahoo Auctions Japan**: auto-translated queries (query_ja override per
  watchlist entry), Buyee purchase links, proxy-fee estimate in costs
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
  Watches/Games/Pop Culture), Discovery quarantine, Movers (30d trends),
  human timing ("ends 2h 59m", amber <6h), hyperlinked titles, hidden
  audit column group (M–P)
- SQLite history.db: comp cache (48h), fair-value trend lines, calibration
  data, 7-day guide-price cache

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

## Backlog
Fanatics Collect + Alt scrapers, Mercari JP, velocity/annualized return,
channel-fee model (Probstein/DC Sports/COMC), sales-tax/vault cost model,
inventory & P&L tracker.
