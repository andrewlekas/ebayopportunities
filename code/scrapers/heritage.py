"""Heritage Auctions scraper - best effort HTML parsing.

Heritage search pages are mostly server-rendered. Selectors may need updates
as the site evolves; some prices require a (free) logged-in session.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from models import Listing
from .base import BaseScraper, note_api

log = logging.getLogger(__name__)


class HeritageScraper(BaseScraper):
    site = "heritage"
    warmup_url = "https://sports.ha.com/"

    def __init__(self, config: dict):
        super().__init__(config)
        costs = (config.get("marketplace_costs") or {}).get("heritage") or {}
        self.buyer_fee_rate = float(costs.get("buyer_fee_rate", 0.22))
        self.minimum_buyer_fee = float(
            costs.get("minimum_buyer_fee", 29.0))

    def search_auctions(self, query: str, max_results: int = 50) -> list[Listing]:
        url = (f"https://sports.ha.com/c/search/results.zx?N=0&Nty=1"
               f"&Ntt={quote_plus(query)}&dept=3923&mode=live&layout=list")
        r = self._get(url)
        if not r:
            return []
        return self.parse_html(
            r.text, query, max_results, self.buyer_fee_rate,
            self.minimum_buyer_fee)

    @staticmethod
    def parse_html(html: str, query: str,
                   max_results: int = 50, buyer_fee_rate: float = 0.22,
                   minimum_buyer_fee: float = 29.0) -> list[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        marker = re.search(r"\b(Current Bid|Open for bidding|Sold For)\b",
                           soup.get_text(" ", strip=True), re.I)
        anchors = soup.select("a[href*='/itm/']")
        if not marker and not anchors:
            note_api("heritage/parse", "failed")
            log.warning("heritage: search markup has no recognizable lot schema")
            return []
        note_api("heritage/parse", "ok")
        out = []
        seen = set()
        for a in anchors:
            href = a.get("href") or ""
            if not href or href in seen:
                continue
            title = a.get_text(" ", strip=True)
            if len(title) < 12:
                continue
            row = a
            row_text = ""
            for _ in range(7):
                row = row.parent
                if row is None:
                    break
                row_text = row.get_text(" ", strip=True)
                if re.search(r"Current Bid\s*:", row_text, re.I):
                    break
            m = re.search(r"Current Bid\s*:\s*\$([\d,]+(?:\.\d+)?)",
                          row_text, re.I)
            if not (title and m):
                continue
            if href.startswith("/"):
                href = "https://sports.ha.com" + href
            seen.add(href)
            out.append(Listing(site="heritage", title=title, url=href,
                               current_price=float(m.group(1).replace(",", "")),
                               query=query, marketplace="HERITAGE",
                               buyer_fee_rate=buyer_fee_rate,
                               minimum_buyer_fee=minimum_buyer_fee))
            if len(out) >= max_results:
                break
        if not out:
            log.info("heritage: 0 currently-biddable results for %r", query)
        return out
