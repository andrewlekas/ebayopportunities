"""Goldin live-auction scraper using the current public search service."""
from __future__ import annotations

import logging
from datetime import datetime

from models import Listing
from .base import BaseScraper, note_api

log = logging.getLogger(__name__)

SEARCH_URL = "https://d1wu47wucybvr3.cloudfront.net/api/lots_v2"


class GoldinScraper(BaseScraper):
    site = "goldin"

    def __init__(self, config: dict):
        super().__init__(config)
        costs = (config.get("marketplace_costs") or {}).get("goldin") or {}
        self.minimum_buyer_fee = float(
            costs.get("minimum_buyer_fee", 19.0))

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
                out.append(Listing(
                    site="goldin", title=title,
                    url=f"https://goldin.co/item/{slug}",
                    current_price=price,
                    bid_count=int(it.get("number_of_bids") or 0),
                    end_time=end, listing_id=str(it.get("lot_id") or ""),
                    query=query, marketplace="GOLDIN",
                    buyer_fee_rate=premium,
                    minimum_buyer_fee=(
                        self.minimum_buyer_fee if premium else 0.0),
                ))
            except (TypeError, ValueError):
                continue
        if not out:
            log.info("goldin: 0 live results for %r", query)
        return out
