"""Goldin live-auction scraper using the current public search service."""
from __future__ import annotations

import logging
import re
from datetime import datetime

from models import Listing
from .base import BaseScraper, note_api

log = logging.getLogger(__name__)

SEARCH_URL = "https://d1wu47wucybvr3.cloudfront.net/api/lots_v2"

NON_CARD_RE = re.compile(
    r"\b(box|case|pack|wax|jersey|helmet|shoe|bat|ball|puck|ticket|"
    r"memorabilia|game-used|game used|watch|poster|print)\b", re.I)
CARD_RE = re.compile(
    r"\b(card|topps|bowman|fleer|panini|pokemon|upper deck|donruss|"
    r"prizm|psa|bgs|sgc)\b", re.I)


def _single_card(title: str) -> bool:
    return bool(CARD_RE.search(title or "")) and not bool(
        NON_CARD_RE.search(title or ""))


class GoldinScraper(BaseScraper):
    site = "goldin"
    capabilities = frozenset({"auctions"})

    def __init__(self, config: dict):
        super().__init__(config)
        costs = (config.get("marketplace_costs") or {}).get("goldin") or {}
        self.minimum_buyer_fee = float(
            costs.get("minimum_buyer_fee", 19.0))
        # Goldin's published domestic card shipping is $6 below $1,000 and
        # $19 at/above $1,000.  Insurance is 0.9% of the price realized.
        # current_price is the hammer/current bid; the separate buyer premium
        # below completes the known landed-cost stack.
        self.shipping_under_1000 = float(
            costs.get("shipping_under_1000", 6.0))
        self.shipping_over_1000 = float(
            costs.get("shipping_over_1000", 19.0))
        self.insurance_rate = float(costs.get("insurance_rate", 0.009))

    def search_auctions(self, query: str, max_results: int = 50) -> list[Listing]:
        payload = {"search": {
            "queryType": "Search", "keyword": query,
            "size": min(max_results, 100), "from": 0,
        }}
        r = self._post(
            SEARCH_URL, api=True, json=payload,
            headers={"Content-Type": "application/json",
                     "Origin": "https://goldin.co",
                     "Referer": "https://goldin.co/"})
        if not r:
            return []
        try:
            data = r.json()
        except ValueError:
            log.warning("goldin: non-JSON response; endpoint likely changed")
            note_api("goldin/parse", "failed")
            return []
        search = data.get("searchalgolia")
        if not isinstance(search, dict) or not isinstance(
                search.get("lots"), list):
            log.warning("goldin: JSON schema changed (missing searchalgolia.lots)")
            note_api("goldin/parse", "failed")
            return []
        note_api("goldin/parse", "ok")
        items = search["lots"]
        out = []
        for it in items:
            try:
                if str(it.get("status") or "").lower() != "live":
                    continue
                title = it.get("title") or ""
                if not title:
                    continue
                price = float(it.get("current_price") or
                              it.get("min_bid_price") or 0)
                end_raw = it.get("end_timestamp")
                end = None
                if end_raw:
                    try:
                        end = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
                    except ValueError:
                        pass
                slug = it.get("meta_slug") or it.get("lot_id") or ""
                premium = max(0.0, float(it.get("buyer_premium") or 0)) / 100
                minimum = self.minimum_buyer_fee if premium else 0.0
                buyer_fee = max(price * premium, minimum)
                # Goldin describes the threshold and insurance in terms of
                # price realized (hammer plus premium). The $6 tier applies
                # only to single cards; other lots use the configurable $19
                # floor because their actual shipping is lot-specific.
                price_realized = price + buyer_fee
                shipping = (
                    self.shipping_under_1000
                    if (_single_card(title) and price_realized < 1000)
                    else self.shipping_over_1000)
                out.append(Listing(
                    site="goldin", title=title,
                    url=f"https://goldin.co/item/{slug}",
                    current_price=price,
                    shipping=shipping,
                    bid_count=int(it.get("number_of_bids") or 0),
                    end_time=end, listing_id=str(it.get("lot_id") or ""),
                    query=query, marketplace="GOLDIN",
                    buyer_fee_rate=premium,
                    minimum_buyer_fee=minimum,
                    insurance_rate=self.insurance_rate,
                    insurance_on_buyer_fee=True,
                ))
            except (TypeError, ValueError):
                continue
        if not out:
            log.info("goldin: 0 live results for %r", query)
        return out
