# Marketplace Access and Feed Contract

## Current status

| Source | Inventory lanes | Access behavior | Buyer cost modeled |
|---|---|---|---|
| Goldin | Auctions | Live lots API, enabled | 22% lot premium / $19 minimum, $6 or $19 card shipping, 0.9% insurance |
| Pristine Auction | Auctions | Public HTML search, enabled | 17% buyer premium; variable shipping is configurable |
| Fanatics Collect | Auctions + fixed | Authorized endpoint or local export only; disabled until configured | 20% auction premium by default, zero Buy Now premium; feed fields may override |
| ALT | Auctions + fixed | Authorized endpoint or local export only; disabled until configured | 20% auction premium by default, zero fixed-price premium; feed fields may override |

Fanatics Collect and ALT are intentionally not implemented by copying
short-lived browser keys or calling undisclosed private endpoints. Their
connectors will make no network call unless `authorized: true` and an
`endpoint` are both present. A user/platform-provided local export is also
supported and does not contact the marketplace.

Current primary references:

- [Goldin User Agreement](https://goldin.co/useragreement)
- [Fanatics Collect Terms of Use](https://support.fanaticscollect.com/en_us/terms-of-use-r11C70QTge)
- [Fanatics Collect Premier Auction](https://support.fanaticscollect.com/en_us/premier-auction-Sk2jQCmaeg)
- [Fanatics Collect Buy Now fees](https://support.fanaticscollect.com/en_us/buy-now-fees-ry33QCXaxe)
- [ALT Terms of Service](https://www.alt.xyz/terms)
- [Pristine Auction Policy](https://www.pristineauction.com/default/index/auction-policy)

## Enable an authorized endpoint

Put credentials in ignored `secrets.yaml`:

```yaml
api_keys:
  fanatics:
    authorized: true
    endpoint: "https://approved.example/fanatics/search"
    access_token: "..."
  alt:
    authorized: true
    endpoint: "https://approved.example/alt/search"
    access_token: "..."
```

Then add `fanatics_collect` and/or `alt` under `sites:` in `config.yaml`.
The scanner sends a `GET` with `q`, `type` (`auction` or `fixed`), and
`limit` query parameters. Bearer auth is used when `access_token` is set.
For API-key auth, use `api_key` and optional `api_key_header`.

Equivalent environment variables are:

- `CARD_SCANNER_FANATICS_ENDPOINT`
- `CARD_SCANNER_FANATICS_ACCESS_TOKEN`
- `CARD_SCANNER_ALT_ENDPOINT`
- `CARD_SCANNER_ALT_ACCESS_TOKEN`

## Enable a local JSON or CSV export

Set `feed_file` instead of an endpoint:

```yaml
api_keys:
  fanatics:
    feed_file: "imports/fanatics.json"
  alt:
    feed_file: "imports/alt.json"
```

Relative paths resolve from the folder containing `config.yaml`. The file is
loaded once per run and filtered locally for each watchlist query.

## Onboard another authorized source

Use `Onboard Source.command` with a YAML manifest based on
`source_manifests/_example.yaml`. A manifest declares:

- a stable source ID and display name
- auction/fixed-price capabilities
- an authorized endpoint or local JSON/CSV export
- source-column/JSON-path mappings to the normalized listing fields
- buyer premium, shipping, insurance, duty, and FX defaults

After validation, an enabled manifest automatically participates in scans
and receives its own Source Health row. It does not require a Python edit or
an additional `sites:` entry. Run `Check Source Feeds.command` to validate
all installed manifests without making network calls.

Raw credentials are rejected from manifests. Put them in ignored
`secrets.yaml` under `api_keys.<source_id>` or reference environment variable
names with `access_token_env` / `api_key_env`.

## Normalized feed schema

```json
{
  "items": [
    {
      "id": "source-listing-id",
      "status": "live",
      "listing_type": "auction",
      "title": "1986 Fleer Michael Jordan PSA 9",
      "url": "https://marketplace.example/item/123",
      "current_price": 1000,
      "bid_count": 7,
      "end_time": "2026-08-01T02:00:00Z",
      "image_url": "https://...",
      "currency": "USD",
      "marketplace": "PREMIER",
      "shipping": 0,
      "buyer_fee_rate": 0.20,
      "minimum_buyer_fee": 0,
      "insurance_rate": 0,
      "insurance_on_buyer_fee": false,
      "best_offer": false,
      "grader": "PSA",
      "certificate_number": "12345678"
    }
  ]
}
```

Required fields are `title`, `url`, and one supported price field:
`current_price`, `currentPrice`, `current_bid`, `currentBid`, or `price`.
The listing ID can be `listing_id`, `listingId`, `id`, or `uuid`.
CSV feeds use the same canonical field names, or a manifest `field_map` can
translate arbitrary source headers. Dotted paths in `field_map` traverse
nested JSON objects.

Supported listing type aliases:

- Auction: `auction`, `weekly`, `premier`, `lot`
- Fixed: `fixed`, `buy_now`, `buy-now`, `bin`, `marketplace`

An item with both `grader` and `certificate_number` receives a canonical
asset ID such as `psa:12345678`. This can safely collapse the same physical
slab if it appears on multiple platforms. A source may instead provide
`canonical_asset_id` directly. Do not derive this field from title alone.

For local exports, optional `queries` or `query_tags` can contain exact
watchlist queries. Otherwise all meaningful query tokens must occur in the
title.

## Access request language

Ask the marketplace for read-only permission to retrieve active auction and
fixed-price inventory for personal valuation research. Request documented
search parameters, rate limits, authentication, current bid/price, listing
type, end time, canonical asset or certificate identity, fees, shipping, and
stable item URLs. State that the integration will obey their rate limits and
will not bid, purchase, list, or modify account data.
