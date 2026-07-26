"""Fanatics Collect scraper (Algolia-backed search).

The site's search is powered by Algolia. We replicate the exact request the
web app makes:
    POST https://{APP_ID}-dsn.algolia.net/1/indexes/*/queries
    index prod_item_state_v1, filtered to live listings.

Algolia's search key is a PUBLIC, search-only key shipped to every browser -
using it is standard and safe. Put it in config.yaml under
api_keys.fanatics (app_id + search_key). To grab the key: open
fanaticscollect.com, DevTools > Network, search a card, click the request to
"*-dsn.algolia.net", copy the "x-algolia-api-key" header value.

Returns live auction listings. Algolia carries current bid/price and bid
count but not the exact end time, so end_time is left unset (the engine's
capture model handles unknown-end listings conservatively).
"""
from __future__ import annotations

import logging

from models import Listing
from .base import BaseScraper

log = logging.getLogger(__name__)

DEFAULT_APP_ID = "3xt9c4x62i"
INDEX = "prod_item_state_v1"
ATTRS = ["listingUuid", "marketplace", "marketplaceSource", "title",
         "subtitle", "currentPrice", "status", "lotNumber", "bidCount"]


def _slug(title: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80]


class FanaticsCollectScraper(BaseScraper):
    site = "fanatics_collect"

    def __init__(self, config: dict):
        super().__init__(config)
        creds = (config.get("api_keys", {}).get("fanatics") or {})
        self.app_id = creds.get("app_id") or DEFAULT_APP_ID
        self.search_key = creds.get("search_key")

    def _algolia(self, query: str, marketplace: str, max_results: int):
        if not self.search_key:
            log.info("fanatics_collect: no search_key in config - skipping "
                     "(see scrapers/fanatics_collect.py for how to get one)")
            return None
        # circuit breaker: failures were counted below but never checked,
        # so a dead/rotated key kept getting POSTed once per query all run
        if self.lane_tripped("api"):
            if "api" not in self._announced:
                log.warning("fanatics_collect/api: %d consecutive failures "
                            "- skipping this channel for the rest of the "
                            "run", self._streaks["api"])
                self._announced.add("api")
            return None
        url = f"https://{self.app_id}-dsn.algolia.net/1/indexes/*/queries"
        headers = {
            "X-Algolia-Application-Id": self.app_id,
            "X-Algolia-API-Key": self.search_key,
            "Content-Type": "application/json",
            "Origin": "https://www.fanaticscollect.com",
            "Referer": "https://www.fanaticscollect.com/",
        }
        payload = {"requests": [{
            "indexName": INDEX, "query": query, "page": 0,
            "hitsPerPage": min(max_results, 48),
            "attributesToRetrieve": ATTRS, "attributesToHighlight": [],
            "filters": f'(marketplace:"{marketplace}") AND (status:"Live")',
        }]}
        import time
        import random
        time.sleep(random.uniform(0.05, 0.2))
        try:
            r = self.session.post(url, json=payload, headers=headers, timeout=30)
            r.raise_for_status()
            self._streaks["api"] = 0
            return r.json()
        except Exception as e:
            self._streaks["api"] += 1
            log.warning("fanatics_collect: search failed (%d/%d) (%s)",
                        self._streaks["api"], self.trip_after, e)
            return None

    def _parse(self, data, query: str) -> list[Listing]:
        out = []
        for res in (data or {}).get("results", []):
            for h in res.get("hits", []):
                title = h.get("title") or ""
                price = h.get("currentPrice")
                uuid = h.get("listingUuid")
                if not (title and uuid and price):
                    continue
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    continue
                out.append(Listing(
                    site="fanatics_collect", title=title,
                    url=f"https://www.fanaticscollect.com/weekly/{uuid}/{_slug(title)}",
                    current_price=price,
                    bid_count=int(h.get("bidCount") or 0),
                    listing_id=uuid, query=query,
                    marketplace=h.get("marketplace", "WEEKLY"),
                    listing_type="fixed" if h.get("marketplace") == "FIXED"
                                 else "auction"))
        return out

    def search_auctions(self, query: str, max_results: int = 50) -> list[Listing]:
        if self.lane_tripped("api"):
            return []
        out = []
        for mp in ("WEEKLY", "PREMIER"):
            data = self._algolia(query, mp, max_results)
            if data:
                out += self._parse(data, query)
        if not out:
            log.info("fanatics_collect: 0 results for %r", query)
        return out

    def search_fixed(self, query: str, max_results: int = 50) -> list[Listing]:
        if self.lane_tripped("api"):
            return []
        data = self._algolia(query, "FIXED", max_results)
        return self._parse(data, query) if data else []
