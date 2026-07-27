# Pokemon Card Auction EV Scanner

Scans live auctions and fixed-price marketplaces (eBay, Goldin, Heritage,
Pristine Auction, plus authorized Fanatics Collect and ALT feeds) for
collectibles on your watchlist, computes a blended fair value, and writes an
Excel report of opportunities sorted by expected value.

## Quick start

```bash
pip install -r requirements.txt
python main.py --demo          # verify everything works (synthetic data)
python main.py                 # live scan using config.yaml
```

Output: `opportunities.xlsx` — sorted by Opportunity Score, color-scaled EV,
clickable listing links, filterable headers, and a persistent Trade Blotter
snapshot.

On macOS, `Open Trade Blotter.command` opens the private CSV where workflow
status and actual trade cash flows survive future scans. `Onboard
Source.command` installs a validated authorized-source manifest, and `Check
Source Feeds.command` validates all installed manifests without contacting
marketplaces.

## Configure

Edit `config.yaml` for ordinary settings. Keep credentials in an ignored
`secrets.yaml` copied from `secrets.example.yaml`:

- **watchlist** — search queries (include grade for graded cards, e.g.
  `"Charizard Base Set Holo PSA 8"`) with optional `max_buy_price`.
- **api_keys** — all optional:
  - `ebay` (client id/secret from developer.ebay.com): uses the stable Browse
    API instead of HTML scraping. Strongly recommended.
  - `pricecharting` token: graded-card guide prices.
  - `pokemontcg` key: TCGPlayer market prices for raw cards.
  - `fanatics` / `alt`: an explicitly authorized inventory endpoint or a
    normalized local JSON/CSV export. See `PLATFORM_ACCESS.md`.
- **algorithm** — tuning knobs (settle ratio, fees, outlier threshold, etc.).
- **trade_blotter** — private CSV path and per-run auto-capture count.
- **source_manifests** — additional authorized JSON/CSV feeds with declared
  capabilities, field maps, and buyer economics; enabled manifests join scans
  automatically.

Environment variables can override the secrets file:
`CARD_SCANNER_EBAY_CLIENT_ID`, `CARD_SCANNER_EBAY_CLIENT_SECRET`,
`CARD_SCANNER_PRICECHARTING_TOKEN`, `CARD_SCANNER_POKEMONTCG_API_KEY`,
`CARD_SCANNER_FANATICS_ENDPOINT`, `CARD_SCANNER_FANATICS_ACCESS_TOKEN`,
`CARD_SCANNER_ALT_ENDPOINT`, `CARD_SCANNER_ALT_ACCESS_TOKEN`,
`CARD_SCANNER_TELEGRAM_BOT_TOKEN`, and
`CARD_SCANNER_TELEGRAM_CHAT_ID`. Set `CARD_SCANNER_SECRETS_FILE` to use a
secrets file in a different location.

## How the valuation works

1. **Sold comps** (eBay completed sales): fuzzy title matching filters wrong
   cards, a grade guard drops mismatched grades, MAD outlier rejection kills
   shill/damaged sales, then a recency-weighted median (30-day half-life)
   gives the comps value. Broad searches discover inventory, but numbered
   cards receive a separate, cache-first sold search for their exact card
   number and listing grade before they can become tradeable.
2. **Price guide**: PriceCharting (graded) or pokemontcg.io (raw) reference.
3. **Blend**: comps weight scales with sample size and tightness; thin/noisy
   comps lean on the guide.
4. **Expected cost**: auctions are modeled to settle near `fair x 0.92`;
   listings ending within ~6h are anchored to the current bid (that's where
   real bargains live).
5. **EV** = fair value net of resale fees (13.25% default) − expected cost.
6. **Confidence** (0–1) from comp count, dispersion, match quality, and
   comps/guide agreement. **Opportunity Score = EV × Confidence** (sort key).

## Notes & caveats

- eBay HTML fallback works without keys but is fragile and rate-limited; use
  API keys for anything serious.
- Goldin uses its live lots service. Pristine uses its public server-rendered
  search. Heritage remains best-effort HTML. Fanatics and ALT deliberately
  make no marketplace request without approved access or a local export.
- Goldin landed cost includes its 22%/$19 premium, published $6/$19 card
  shipping tiers, and 0.9% insurance on hammer plus premium. Non-card Goldin
  lots use $19 as a configurable shipping floor. Pristine includes its 17% premium;
  its variable shipping is configurable and defaults to zero, so verify it
  before bidding.
- Respect each site's terms of service and robots.txt; the built-in request
  delay is deliberate. This tool is for research, not high-frequency scraping.
- Valuations are estimates, not financial advice — always verify a comp
  yourself before bidding.
