"""Japan marketplace scraper - Yahoo! Auctions JP (+ PayPay Flea Market),
scraped via Buyee, the proxy-buying service.

The arbitrage: buy in Japan (via Buyee, no JP address needed), sell into the
US market. Fair values still come from US eBay comps, so the spread is
visible directly in the report.

Why Buyee and not auctions.yahoo.co.jp directly: Yahoo JP serves EMPTY pages
to non-browser HTTP clients (verified 2026-07-16 - that's why this scraper
found almost nothing for weeks). Buyee mirrors the same Yahoo Auctions
inventory, is server-rendered, welcomes overseas traffic, and its listing
pages are where we'd send Andrew to buy anyway. Structure (verified live):

  li.itemCard
    .itemCard__itemName                       <- title
    a[href*="/item/jdirectitems/auction/"]    <- Yahoo auction (ID like b123..)
    a[href*="/paypayfleamarket/item/"]        <- PayPay listing (fixed price)
    .g-priceDetails__item                     <- first = current price
      .g-price                                <- "50,000 YEN"

Notes:
- Queries are auto-translated (English Pokemon names -> Japanese) via the
  map below. For anything not in the map, add a `query_ja` field to the
  watchlist entry - a native Japanese query always beats auto-translation.
- Proxy, domestic/international shipping, insurance, currency spread and
  import duty are modeled separately under ``japan`` in config. These are
  estimates: verify the final proxy quote and customs classification.
- JP graded cards use PSA too, so grade matching works. "1st Edition"
  doesn't exist on JP vintage (the no-rarity/old-back era plays that role).
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from models import Listing
from .base import BaseScraper, note_api

log = logging.getLogger(__name__)

SEARCH_URL = "https://buyee.jp/item/search/query/{q}?translationType=98"
AUCTION_ID_RE = re.compile(r"/jdirectitems/auction/([a-z]\d+)", re.I)
PAYPAY_ID_RE = re.compile(r"/paypayfleamarket/item/([a-z0-9]+)", re.I)
YEN_RE = re.compile(r"([\d,]+)\s*YEN", re.I)

# English -> Japanese for common watchlist terms. Extend freely.
JA_MAP = {
    "charizard": "リザードン", "blastoise": "カメックス", "venusaur": "フシギバナ",
    "pikachu": "ピカチュウ", "raichu": "ライチュウ", "gyarados": "ギャラドス",
    "gengar": "ゲンガー", "dragonite": "カイリュー", "snorlax": "カビゴン",
    "flareon": "ブースター", "jolteon": "サンダース", "vaporeon": "シャワーズ",
    "chansey": "ラッキー", "poliwrath": "ニョロボン", "umbreon": "ブラッキー",
    "pidgeotto": "ピジョン", "pokemon": "ポケモンカード",
    "topsun": "トップサン", "no rarity": "マークなし", "base set": "旧裏",
    "movie promo": "映画 プロモ", "red cheeks": "赤ほっぺ",
    # terms that don't exist / hurt recall on the JP market:
    "1st edition": "", "1999": "", "holo": "", "jungle": "ジャングル",
    "fossil": "化石",
}


def translate_query(query: str) -> str:
    q = query.lower()
    for en in sorted(JA_MAP, key=len, reverse=True):
        q = q.replace(en, JA_MAP[en])
    q = re.sub(r"\s+", " ", q).strip()
    return q


class YahooJpScraper(BaseScraper):
    site = "yahoo_jp"
    # The Buyee result page is a mixed Yahoo Auctions/PayPay feed exposed
    # through search_auctions; keep it on one lane to avoid fetching the
    # identical page twice during a full scan.
    capabilities = frozenset({"auctions"})

    def __init__(self, config: dict):
        super().__init__(config)
        self.fx_jpy = (config.get("fx_rates") or {}).get("JPY", 0.0063)
        jp = config.get("japan") or {}
        self.proxy_fee = jp.get("proxy_fee_usd", 10.0)
        self.domestic_shipping = jp.get("domestic_shipping_usd", 8.0)
        self.international_shipping = jp.get(
            "international_shipping_usd", 35.0)
        self.insurance_rate = jp.get("insurance_rate", 0.01)
        self.import_duty_rate = jp.get("import_duty_rate", 0.15)
        self.fx_spread_rate = jp.get("fx_spread_rate", 0.03)

    def search_auctions(self, query: str, max_results: int = 50,
                        query_ja: str | None = None) -> list[Listing]:
        q = query_ja or translate_query(query)
        r = self._get(SEARCH_URL.format(q=quote(q)),
                      headers={"Accept-Language": "en"})
        if not r:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("li.itemCard")
        if not cards:
            # empty page or layout change: make it loud enough to notice
            note_api("yahoo_jp/parse", "failed")
            log.warning("yahoo_jp/buyee: no item cards for %r (ja: %r) - "
                        "empty result or markup changed", query, q)
            return []
        note_api("yahoo_jp/parse", "ok")
        out = []
        for it in cards:
            title_el = it.select_one(".itemCard__itemName")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            a = it.select_one("a[href*='/jdirectitems/auction/'], "
                              "a[href*='/paypayfleamarket/item/']")
            if not (title and a):
                continue
            href = a.get("href", "")
            am = AUCTION_ID_RE.search(href)
            pm = PAYPAY_ID_RE.search(href)
            if not (am or pm):
                continue
            listing_id = (am or pm).group(1)
            # first price block = current price (auctions may also show a
            # second "Buyout Price" block - that's not the cost basis)
            price_el = (it.select_one(".g-priceDetails__item .g-price")
                        or it.select_one(".g-price"))
            ym = YEN_RE.search(price_el.get_text()) if price_el else None
            if not ym:
                continue
            yen = float(ym.group(1).replace(",", ""))
            if yen <= 0:
                continue
            out.append(Listing(
                site="yahoo_jp", title=title,
                url=f"https://buyee.jp{href.split('?')[0]}",
                current_price=round(yen * self.fx_jpy, 2),
                shipping=self.domestic_shipping,
                buyer_fees=self.proxy_fee,
                international_shipping=self.international_shipping,
                insurance_rate=self.insurance_rate,
                import_duty_rate=self.import_duty_rate,
                fx_spread_rate=self.fx_spread_rate,
                bid_count=0,                 # not shown on Buyee cards
                listing_id=listing_id, query=query,
                listing_type="auction" if am else "fixed",
                currency="JPY",
                marketplace="YAHOO_JP" if am else "PAYPAY_JP",
            ))
            if len(out) >= max_results:
                break
        if not out:
            log.info("yahoo_jp: 0 parsed results for %r (ja: %r)", query, q)
        return out
