"""Goldin Auctions scraper (best effort).

Goldin's site is JavaScript-rendered; this hits their public search JSON
endpoint. Auction sites change their internal APIs without notice - if this
stops returning results, inspect network traffic on goldin.co search pages
and update SEARCH_URL / the JSON field names below.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from models import Listing
from .base import BaseScraper

log = logging.getLogger(__name__)

SEARCH_URL = "https://app.goldin.co/api/search/items"


class GoldinScraper(BaseScraper):
    site = "goldin"

    def search_auctions(self, query: str, max_results: int = 50) -> list[Listing]:
        r = self._get(SEARCH_URL, params={"query": query, "size": max_results,
                                          "saleType": "auction"})
        if not r:
            return []
        try:
            data = r.json()
        except ValueError:
            log.warning("goldin: non-JSON response; endpoint likely changed")
            return []
        items = data.get("items") or data.get("hits") or data.get("results") or []
        out = []
        for it in items:
            try:
                title = it.get("title") or it.get("name") or ""
                if not title:
                    continue
                price = float(it.get("currentBid") or it.get("current_bid")
                              or it.get("price") or 0)
                end_raw = it.get("endsAt") or it.get("end_time") or it.get("endTime")
                end = None
                if end_raw:
                    try:
                        end = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
                    except ValueError:
                        pass
                slug = it.get("slug") or it.get("id") or ""
                out.append(Listing(
                    site="goldin", title=title,
                    url=f"https://goldin.co/item/{slug}",
                    current_price=price,
                    bid_count=int(it.get("bidCount") or it.get("bids") or 0),
                    end_time=end, listing_id=str(it.get("id", "")), query=query,
                ))
            except (TypeError, ValueError):
                continue
        if not out:
            log.info("goldin: 0 results for %r (endpoint may have changed)", query)
        return out
