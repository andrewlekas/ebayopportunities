"""Pristine Auction public live-search connector.

The search page is server-rendered and exposes stable data attributes for the
lot ID, current bid, image, and Unix end timestamp.  Requests go through the
shared polite HTML lane and its persistent circuit breaker.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from models import Listing
from .base import BaseScraper, note_api

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.pristineauction.com/auction/search/"
LOT_RE = re.compile(r"^/a(\d+)-")


class PristineScraper(BaseScraper):
    site = "pristine"
    capabilities = frozenset({"auctions"})
    warmup_url = "https://www.pristineauction.com/"

    def __init__(self, config: dict):
        super().__init__(config)
        costs = (config.get("marketplace_costs") or {}).get("pristine") or {}
        self.buyer_fee_rate = float(costs.get("buyer_fee_rate", 0.17))
        # Shipping varies by size, value, origin, and invoice combination.
        # Keep this explicit/configurable rather than pretending it is known.
        self.shipping_estimate = float(costs.get("shipping_estimate", 0.0))

    def search_auctions(self, query: str,
                        max_results: int = 50) -> list[Listing]:
        # The page-size control accepts 15/30/60, not arbitrary values.
        page_size = 15 if max_results <= 15 else (
            30 if max_results <= 30 else 60)
        params = {
            "term": query,
            "category": "",
            "sort_method": "ending-soonest",
            "per_page": page_size,
        }
        response = self._get(
            f"{SEARCH_URL}?{urlencode(params)}",
            headers={"Accept-Language": "en-US,en;q=0.9"})
        if not response:
            return []
        return self.parse_html(
            response.text, query, max_results,
            self.buyer_fee_rate, self.shipping_estimate)

    @staticmethod
    def parse_html(html: str, query: str, max_results: int = 50,
                   buyer_fee_rate: float = 0.17,
                   shipping_estimate: float = 0.0) -> list[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("div.row.product[aria-label='Auction item']")
        if not cards:
            text = soup.get_text(" ", strip=True)
            if re.search(r"\b(?:0|no)\s+results?\s+found\b", text, re.I):
                note_api("pristine/parse", "ok")
            else:
                note_api("pristine/parse", "failed")
                log.warning(
                    "pristine: search markup has no recognizable lot schema")
            return []
        note_api("pristine/parse", "ok")
        output = []
        seen = set()
        now = datetime.now(timezone.utc)
        for card in cards:
            title_link = card.select_one("a.title[href]")
            href = title_link.get("href", "") if title_link else ""
            match = LOT_RE.match(href)
            if not match:
                continue
            listing_id = (
                card.get("data-pristine-product-venue-id") or match.group(1))
            if listing_id in seen:
                continue
            title = (card.get("data-pristine-title")
                     or title_link.get_text(" ", strip=True))
            price_element = card.select_one(".high-bid[data-high-bid]")
            end_element = card.select_one(
                ".end-time[data-pristine-end-time]")
            if not (title and price_element and end_element):
                continue
            try:
                price = float(price_element["data-high-bid"])
                end_time = datetime.fromtimestamp(
                    float(end_element["data-pristine-end-time"]),
                    tz=timezone.utc)
            except (KeyError, TypeError, ValueError, OSError):
                continue
            if price < 0 or end_time <= now:
                continue
            image = card.select_one("img[src]")
            seen.add(listing_id)
            output.append(Listing(
                site="pristine", title=title.strip(),
                url="https://www.pristineauction.com" + href,
                current_price=price, shipping=shipping_estimate,
                end_time=end_time,
                image_url=image.get("src", "") if image else "",
                listing_id=str(listing_id), query=query,
                listing_type="auction", marketplace="PRISTINE",
                buyer_fee_rate=buyer_fee_rate,
            ))
            if len(output) >= max_results:
                break
        if not output:
            log.info("pristine: 0 live parsed results for %r", query)
        return output
