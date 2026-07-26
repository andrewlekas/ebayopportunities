# Pokemon Card Auction EV Scanner

Scans live auctions (eBay, Goldin, Fanatics Collect, Heritage) for cards on
your watchlist, computes a blended fair value, and writes an Excel report of
opportunities sorted by expected value.

## Quick start

```bash
pip install -r requirements.txt
python main.py --demo          # verify everything works (synthetic data)
python main.py                 # live scan using config.yaml
```

Output: `opportunities.xlsx` — sorted by Opportunity Score, color-scaled EV,
clickable listing links, filterable header.

## Configure

Edit `config.yaml`:

- **watchlist** — search queries (include grade for graded cards, e.g.
  `"Charizard Base Set Holo PSA 8"`) with optional `max_buy_price`.
- **api_keys** — all optional:
  - `ebay` (client id/secret from developer.ebay.com): uses the stable Browse
    API instead of HTML scraping. Strongly recommended.
  - `pricecharting` token: graded-card guide prices.
  - `pokemontcg` key: TCGPlayer market prices for raw cards.
- **algorithm** — tuning knobs (settle ratio, fees, outlier threshold, etc.).

## How the valuation works

1. **Sold comps** (eBay completed sales): fuzzy title matching filters wrong
   cards, a grade guard drops mismatched grades, MAD outlier rejection kills
   shill/damaged sales, then a recency-weighted median (30-day half-life)
   gives the comps value.
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
- Goldin/Fanatics/Heritage are JS-heavy sites — those scrapers are best-effort
  and fail gracefully. If they return 0 results, inspect the site's network
  requests and update the endpoint/field names in `scrapers/<site>.py`.
- Respect each site's terms of service and robots.txt; the built-in request
  delay is deliberate. This tool is for research, not high-frequency scraping.
- Valuations are estimates, not financial advice — always verify a comp
  yourself before bidding.
