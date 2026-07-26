"""eBay scraper.

Modes:
  1. Official Browse API (preferred) - auctions AND fixed-price/best-offer,
     across multiple marketplaces (EBAY_US, EBAY_GB, EBAY_DE, ...), with
     foreign prices converted to USD via config fx_rates.
  2. HTML fallback - public search pages; fragile, US-only.

Sold comps come from eBay's completed/sold search pages.
"""
from __future__ import annotations

import base64
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from models import Listing, SoldComp
from .base import BaseScraper, note_api

log = logging.getLogger(__name__)

OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

PRICE_RE = re.compile(r"[\d,]+\.?\d*")
DEFAULT_FX = {"USD": 1.0, "GBP": 1.27, "EUR": 1.08, "CAD": 0.73,
              "AUD": 0.65, "JPY": 0.0063, "CHF": 1.12}


def _parse_price(text: str) -> float | None:
    m = PRICE_RE.search(text or "")
    return float(m.group().replace(",", "")) if m else None


def _iso(dt_str: str | None):
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return None


class EbayScraper(BaseScraper):
    site = "ebay"
    warmup_url = "https://www.ebay.com/"

    def __init__(self, config: dict):
        super().__init__(config)
        creds = config.get("api_keys", {}).get("ebay", {}) or {}
        self.client_id = creds.get("client_id")
        self.client_secret = creds.get("client_secret")
        self.marketplaces = config.get("marketplaces") or ["EBAY_US"]
        self.fx = {**DEFAULT_FX, **(config.get("fx_rates") or {})}
        self._token = None
        # OAuth circuit breaker: without this, a dead auth endpoint gets
        # re-POSTed on every single query (~200 calls/run). Rule: any
        # endpoint that fails 3 straight gets left alone for the run.
        self._oauth_fails = 0

    # ---------------- Browse API ----------------
    def _get_token(self) -> str | None:
        if self._token:
            return self._token
        if not (self.client_id and self.client_secret):
            return None
        if self._oauth_fails >= 3:
            note_api("ebay/oauth", "skipped")
            return None          # breaker tripped - announced at fail #3
        auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        try:
            r = self.session.post(
                OAUTH_URL,
                headers={"Authorization": f"Basic {auth}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials",
                      "scope": "https://api.ebay.com/oauth/api_scope"},
                timeout=30,
            )
            r.raise_for_status()
            self._token = r.json()["access_token"]
            self._oauth_fails = 0
            note_api("ebay/oauth", "ok")
            return self._token
        except Exception as e:
            note_api("ebay/oauth", "failed")
            self._oauth_fails += 1
            log.warning("ebay: OAuth failed (%d/3) (%s); falling back to "
                        "HTML", self._oauth_fails, e)
            if self._oauth_fails == 3:
                log.warning("ebay: OAuth failed 3 straight times - not "
                            "retrying for the rest of this run")
            return None

    def _usd(self, money: dict | None) -> tuple[float, str]:
        """(usd_amount, original_currency) from a Browse API money object."""
        if not money:
            return 0.0, "USD"
        val = float(money.get("value", 0) or 0)
        cur = money.get("currency", "USD") or "USD"
        return val * self.fx.get(cur, 1.0), cur

    def _parse_summary(self, it: dict, query: str, mp: str) -> Listing | None:
        title = it.get("title", "")
        if not title:
            return None
        buying = it.get("buyingOptions") or []
        is_auction = "AUCTION" in buying
        money = it.get("currentBidPrice") if is_auction else it.get("price")
        price, cur = self._usd(money or it.get("price"))
        if price <= 0:
            return None
        # Hybrid (auction + buy-it-now): `price` is the BIN, which is the
        # only price actually transactable while the bid count is zero.
        # It used to be discarded, so a zero-bid hybrid showed the seller's
        # opening bid as its cost basis and invented edge that way.
        has_bin = is_auction and "FIXED_PRICE" in buying
        bin_price = 0.0
        if has_bin:
            bin_price, _ = self._usd(it.get("price"))
        ship = 0.0
        for opt in it.get("shippingOptions", []) or []:
            ship, _ = self._usd(opt.get("shippingCost"))
            break
        seller_fb = (it.get("seller") or {}).get("feedbackScore")
        return Listing(
            site="ebay", title=title, url=it.get("itemWebUrl", ""),
            current_price=round(price, 2), shipping=round(ship, 2),
            bid_count=int(it.get("bidCount", 0) or 0),
            end_time=_iso(it.get("itemEndDate")),
            image_url=(it.get("image") or {}).get("imageUrl", ""),
            listing_id=it.get("itemId", ""), query=query,
            listing_type="auction" if is_auction else "fixed",
            best_offer="BEST_OFFER" in buying,
            has_buy_now=has_bin,
            buy_now_price=round(bin_price, 2),
            created_at=_iso(it.get("itemCreationDate")),
            currency=cur, marketplace=mp,
            seller_feedback=int(seller_fb) if seller_fb is not None else None,
        )

    def _search_api(self, query: str, max_results: int, *,
                    buying: str, sort: str | None = None,
                    intl: bool = True) -> list[Listing]:
        token = self._get_token()
        if not token:
            return []
        mps = self.marketplaces if intl else self.marketplaces[:1]

        def fetch(mp: str) -> list[Listing]:
            params = {"q": query, "filter": f"buyingOptions:{{{buying}}}",
                      "limit": min(max_results, 200)}
            if sort:
                params["sort"] = sort
            r = self._get(BROWSE_URL, api=True,
                          headers={"Authorization": f"Bearer {token}",
                                   "X-EBAY-C-MARKETPLACE-ID": mp},
                          params=params)
            if not r:
                return []
            found = []
            for it in r.json().get("itemSummaries", []) or []:
                l = self._parse_summary(it, query, mp)
                if l:
                    found.append(l)
            return found

        if len(mps) == 1:
            return fetch(mps[0])
        out = []
        with ThreadPoolExecutor(max_workers=len(mps)) as ex:
            for found in ex.map(fetch, mps):
                out.extend(found)
        return out

    # ---------------- HTML fallback ----------------
    def _search_html(self, query: str, max_results: int, *,
                     sold: bool = False, fixed: bool = False):
        flag = ("&LH_Sold=1&LH_Complete=1" if sold
                else "&LH_BIN=1&_sop=10" if fixed      # newly listed BINs
                else "&LH_Auction=1")
        url = (f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(query)}"
               f"&_ipg=120" + flag)
        CHALLENGE_MARKERS = ("pardon our interruption",
                             "splashui/challenge",
                             "please verify yourself",
                             "checking your browser",
                             "reference id:")

        def challenged(resp) -> bool:
            # eBay bot-blocks return HTTP 200 with a challenge page, which
            # raise_for_status() can't catch - detect it so the circuit
            # breaker trips instead of silently parsing 0 items all run
            low = resp.text[:4000].lower()
            return any(m in low for m in CHALLENGE_MARKERS)

        r = self._get(url)
        if not r:
            return []
        if challenged(r):
            self._streaks["html"] += 1
            log.warning("ebay/html: bot-challenge page detected "
                        "(%d/%d, %d this run) for %s", self._streaks["html"],
                        self.trip_after, self._challenge_count + 1, url)
            # a cleared challenge resets the failure streak below, but the
            # run-wide challenge tally never resets: 10 challenges in one
            # run = eBay wants us gone - back off hard (persists cross-run)
            if self.note_challenge("html"):
                return []
            # the lane may have been tripped by a parallel query's challenge
            # while this one was in flight - don't burn a 20s cooldown and
            # a retry against a site we've already decided to leave alone
            if self.lane_tripped("html"):
                return []
            # challenges are usually transient (tripped by request bursts):
            # cool down once and retry before giving up on this query
            cooldown = (self.config.get("scraping", {})
                        .get("challenge_cooldown_seconds", 20))
            time.sleep(cooldown * random.uniform(0.7, 1.3))
            if self.lane_tripped("html"):   # tripped during the cooldown
                return []
            r = self._get(url)
            if not r or challenged(r):
                log.warning("ebay/html: still challenged after %ds cooldown "
                            "- wiping cookie jar for a fresh start next run",
                            cooldown)
                self.note_challenge("html")
                self.reset_cookies()
                return []
            log.info("ebay/html: challenge cleared after cooldown retry")
            self._streaks["html"] = 0   # recovered - clear the streak
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select("li.s-item, li.s-card")
        results = []
        for it in items[:max_results]:
            a = it.select_one("a.s-item__link, a[href*='/itm/']")
            title_el = it.select_one(".s-item__title, .s-card__title")
            price_el = it.select_one(".s-item__price, .s-card__price")
            if not (a and title_el and price_el):
                continue
            title = title_el.get_text(" ", strip=True)
            if "shop on ebay" in title.lower():
                continue
            price = _parse_price(price_el.get_text())
            if price is None:
                continue
            ship_el = it.select_one(".s-item__shipping, .s-item__logisticsCost")
            ship = _parse_price(ship_el.get_text()) or 0.0 if ship_el else 0.0
            if sold:
                results.append(SoldComp(title=title, price=price + ship,
                                        url=a["href"], site="ebay"))
            else:
                bids_el = it.select_one(".s-item__bids, .s-item__bidCount")
                bids = int(_parse_price(bids_el.get_text()) or 0) if bids_el else 0
                results.append(Listing(
                    site="ebay", title=title, url=a["href"],
                    current_price=price, shipping=ship, bid_count=bids,
                    query=query,
                    listing_type="fixed" if fixed else "auction"))
        return results

    # ---------------- public API ----------------
    def search_auctions(self, query: str, max_results: int = 50,
                        intl: bool = True) -> list[Listing]:
        listings = self._search_api(query, max_results, buying="AUCTION",
                                    intl=intl)
        if listings:
            return listings
        return self._search_html(query, max_results)

    def search_fixed(self, query: str, max_results: int = 50,
                     intl: bool = True) -> list[Listing]:
        """Newly-listed Buy It Now / Best Offer listings."""
        listings = self._search_api(query, max_results,
                                    buying="FIXED_PRICE|BEST_OFFER",
                                    sort="newlyListed", intl=intl)
        if listings:
            return listings
        return self._search_html(query, max_results, fixed=True)

    def search_sold(self, query: str, max_results: int = 60) -> list[SoldComp]:
        comps = self._search_html(query, max_results, sold=True)
        now = datetime.now(timezone.utc)
        for c in comps:
            c.sold_date = c.sold_date or now
        return comps
