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
from .base import BaseScraper

log = logging.getLogger(__name__)
PRICE_RE = re.compile(r"[\d,]+\.?\d*")


class HeritageScraper(BaseScraper):
    site = "heritage"

    def search_auctions(self, query: str, max_results: int = 50) -> list[Listing]:
        url = (f"https://www.ha.com/c/search-results.zx?N=0&Nty=1"
               f"&Ntt={quote_plus(query)}&ic=list")
        r = self._get(url)
        if not r:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select(".search-result, .lot-result, [class*='searchResult']")
        out = []
        for row in rows[:max_results]:
            a = row.select_one("a[href*='/itm/'], a[href*='lot']")
            if not a:
                continue
            title = a.get_text(" ", strip=True)
            price_el = row.select_one("[class*='price'], [class*='bid']")
            m = PRICE_RE.search(price_el.get_text()) if price_el else None
            if not (title and m):
                continue
            href = a["href"]
            if href.startswith("/"):
                href = "https://www.ha.com" + href
            out.append(Listing(site="heritage", title=title, url=href,
                               current_price=float(m.group().replace(",", "")),
                               query=query))
        if not out:
            log.info("heritage: 0 results for %r (selectors may need updating)", query)
        return out
