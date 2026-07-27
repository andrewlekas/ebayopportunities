"""130point.com sold-sales scraper (free comps source).

130point aggregates sold prices across eBay, Fanatics Collect, Goldin, etc.
INCLUDING accepted Best Offer amounts that eBay itself hides - often better
data than eBay's own sold pages.

Site redesigned mid-2026: the old back.130point.com POST endpoint is dead.
The new frontend fetches GET 130point.com/api/search/html?q=...&saleType=sold
which returns an HTML fragment of result cards with machine-readable data
attributes (verified live 2026-07-16):

  <a data-sold-result href="<listing url>">
    <p>TITLE</p>
    <p data-original-price-amount="1700">...</p>   <- crossed-out ask (ignore)
    <p data-price-amount="1600" data-price-currency="USD">...</p>  <- actual sale
    <span data-result-end-time="2026-07-16T20:35:11.000Z">        <- sold date
  </a>

Best effort: parsing is deliberately tolerant and fails to an empty list.
If it returns 0 results while the website works in a browser, the markup
has changed again.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from models import SoldComp
from .base import BaseScraper

log = logging.getLogger(__name__)

SEARCH_URL = "https://130point.com/api/search/html"


def _parse_iso(text: str):
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


class Point130Scraper(BaseScraper):
    site = "130point"
    capabilities = frozenset({"sold"})
    warmup_url = "https://130point.com/sales/"

    def search_sold(self, query: str, max_results: int = 60) -> list[SoldComp]:
        url = f"{SEARCH_URL}?{urlencode({'q': query, 'saleType': 'sold'})}"
        r = self._get(url, headers={"Referer": "https://130point.com/search",
                                    "Accept": "text/html"})
        if not r:
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        comps: list[SoldComp] = []
        skipped_fx = 0
        for card in soup.select("a[data-sold-result]"):
            # actual sale price (data-price-amount) - NOT the crossed-out
            # original ask (data-original-price-amount)
            price_el = card.select_one("[data-price-amount]")
            if not price_el:
                continue
            try:
                price = float(str(price_el.get("data-price-amount"))
                              .replace(",", ""))
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            cur = (price_el.get("data-price-currency") or "USD").upper()
            if cur != "USD":
                skipped_fx += 1
                continue

            title_el = card.find("p")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            if len(title) < 15:
                continue

            date_el = card.select_one("[data-result-end-time]")
            sold_date = (_parse_iso(str(date_el.get("data-result-end-time")))
                         if date_el else None)

            comps.append(SoldComp(
                title=title[:200],
                price=price,
                sold_date=sold_date or datetime.now(timezone.utc),
                url=str(card.get("href") or ""),
                site="130point"))
            if len(comps) >= max_results:
                break

        if skipped_fx:
            log.debug("130point: skipped %d non-USD sales for %r",
                      skipped_fx, query)
        if not comps:
            log.info("130point: 0 comps for %r (site markup may have changed)",
                     query)
        return comps
