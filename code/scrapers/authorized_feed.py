"""Shared boundary for marketplace APIs/exports that require permission.

Fanatics Collect and ALT do not publish anonymous inventory APIs and their
terms restrict unapproved scripted access.  This connector intentionally does
not discover browser keys or scrape private endpoints.  It accepts either:

* an explicitly authorized JSON endpoint, or
* a local JSON export supplied by the user/platform.

The normalized payload is ``{"items": [...]}``; see docs/PLATFORM_ACCESS.md.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import os
import re
from datetime import datetime
from typing import Any

from models import Listing
from .base import BaseScraper, note_api

log = logging.getLogger(__name__)


def _datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _number(item: dict, *names: str, default=0.0) -> float:
    for name in names:
        value = item.get(name)
        if value not in (None, ""):
            try:
                return float(str(value).replace(",", "").replace("$", ""))
            except (TypeError, ValueError):
                continue
    return float(default)


def _integer(item: dict, *names: str, default=0) -> int:
    return int(_number(item, *names, default=default))


def _boolean(item: dict, *names: str, default=False) -> bool:
    for name in names:
        value = item.get(name)
        if value in (None, ""):
            continue
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {
            "1", "true", "yes", "y", "on"}
    return bool(default)


def canonical_asset_id(item: dict) -> str:
    """Return a source-independent asset identity only when trustworthy."""
    explicit = item.get("canonical_asset_id") or item.get("canonicalAssetId")
    if explicit:
        return str(explicit).strip().lower()
    certificate = (item.get("certificate_number")
                   or item.get("certificateNumber")
                   or item.get("cert_number")
                   or item.get("certNumber"))
    grader = item.get("grader") or item.get("grading_company")
    if certificate and grader:
        certificate = re.sub(r"[^a-zA-Z0-9]", "", str(certificate)).lower()
        grader = re.sub(r"[^a-zA-Z0-9]", "", str(grader)).lower()
        if certificate and grader:
            return f"{grader}:{certificate}"
    return ""


class AuthorizedFeedScraper(BaseScraper):
    """Base class for permission-gated normalized marketplace feeds."""

    credentials_key = ""
    display_name = "marketplace"
    default_auction_premium = 0.0
    capabilities = frozenset({"auctions", "fixed"})

    def __init__(self, config: dict):
        super().__init__(config)
        credentials = (
            (config.get("api_keys") or {}).get(self.credentials_key) or {})
        self.authorized = bool(credentials.get("authorized", False))
        self.endpoint = str(credentials.get("endpoint") or "").strip()
        self.access_token = str(
            credentials.get("access_token") or "").strip()
        self.api_key = str(credentials.get("api_key") or "").strip()
        self.api_key_header = str(
            credentials.get("api_key_header") or "X-API-Key").strip()
        feed_file = str(credentials.get("feed_file") or "").strip()
        if feed_file and not os.path.isabs(feed_file):
            feed_file = os.path.join(
                config.get("_config_dir") or os.getcwd(), feed_file)
        self.feed_file = feed_file
        self._file_payload: Any = None
        self._file_loaded = False

    @property
    def access_ready(self) -> bool:
        return bool(self.feed_file or (self.authorized and self.endpoint))

    def _disabled(self):
        marker = "access"
        if marker not in self._announced:
            note_api(f"{self.site}/access", "skipped")
            log.info(
                "%s: no authorized endpoint or local export configured; "
                "skipping without contacting the marketplace",
                self.display_name)
            self._announced.add(marker)
        return None

    def _load_payload(self, query: str, listing_type: str, max_results: int):
        if self.feed_file:
            if not self._file_loaded:
                self._file_loaded = True
                try:
                    with open(
                            self.feed_file, encoding="utf-8-sig",
                            newline="") as handle:
                        if self.feed_file.lower().endswith(".csv"):
                            self._file_payload = {
                                "items": list(csv.DictReader(handle))}
                        else:
                            self._file_payload = json.load(handle)
                    note_api(f"{self.site}/feed", "ok")
                except (OSError, ValueError) as exc:
                    note_api(f"{self.site}/feed", "failed")
                    log.warning("%s: could not read feed export %s (%s)",
                                self.display_name, self.feed_file, exc)
                    self._file_payload = None
            return self._file_payload
        if not (self.authorized and self.endpoint):
            return self._disabled()
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if self.api_key:
            headers[self.api_key_header] = self.api_key
        response = self._get(
            self.endpoint, api=True, headers=headers,
            params={"q": query, "type": listing_type,
                    "limit": min(max_results, 100)})
        if not response:
            return None
        try:
            return response.json()
        except ValueError:
            note_api(f"{self.site}/parse", "failed")
            log.warning("%s: authorized endpoint returned non-JSON data",
                        self.display_name)
            return None

    @staticmethod
    def _items(payload) -> list[dict] | None:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return payload["items"]
        return None

    @staticmethod
    def _matches_export_query(item: dict, query: str) -> bool:
        """Loose local filtering; remote endpoints are expected to filter."""
        tags = item.get("queries") or item.get("query_tags")
        if isinstance(tags, list) and query.lower() in {
                str(tag).lower() for tag in tags}:
            return True
        title = str(item.get("title") or "").lower()
        tokens = re.findall(r"[a-z0-9]+", query.lower())
        meaningful = [token for token in tokens if len(token) > 2]
        if not title or not meaningful:
            return False
        matches = sum(token in title for token in meaningful)
        # Local exports should preserve recall; the scanner's normal
        # subject/grade/variant relevance gates still run after ingestion.
        return matches >= max(1, math.ceil(len(meaningful) * 0.50))

    def _parse(self, payload, query: str, requested_type: str = "auction",
               max_results: int = 50) -> list[Listing]:
        items = self._items(payload)
        if items is None:
            note_api(f"{self.site}/parse", "failed")
            log.warning("%s: feed schema changed (expected items list)",
                        self.display_name)
            return []
        note_api(f"{self.site}/parse", "ok")
        output = []
        for item in items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "live").lower()
            if status not in {"live", "active", "open", "listed"}:
                continue
            listing_type = str(
                item.get("listing_type") or item.get("listingType")
                or item.get("type") or requested_type).lower()
            if listing_type in {"buy_now", "buy-now", "bin", "marketplace"}:
                listing_type = "fixed"
            elif listing_type in {"weekly", "premier", "lot"}:
                listing_type = "auction"
            if listing_type != requested_type:
                continue
            if self.feed_file and not self._matches_export_query(item, query):
                continue
            title = str(item.get("title") or "").strip()
            listing_id = str(
                item.get("listing_id") or item.get("listingId")
                or item.get("id") or item.get("uuid") or "").strip()
            url = str(item.get("url") or item.get("listing_url") or "").strip()
            price = _number(
                item, "current_price", "currentPrice", "current_bid",
                "currentBid", "price", default=-1)
            if not title or not url or price < 0:
                continue
            premium = _number(
                item, "buyer_fee_rate", "buyerFeeRate", default=(
                    self.default_auction_premium
                    if listing_type == "auction" else 0.0))
            if premium > 1:
                premium /= 100
            output.append(Listing(
                site=self.site, title=title, url=url,
                current_price=price,
                shipping=_number(item, "shipping", "shipping_cost"),
                bid_count=_integer(item, "bid_count", "bidCount"),
                end_time=_datetime(
                    item.get("end_time") or item.get("endTime")),
                image_url=str(
                    item.get("image_url") or item.get("imageUrl") or ""),
                listing_id=listing_id,
                canonical_asset_id=canonical_asset_id(item),
                query=query, listing_type=listing_type,
                best_offer=_boolean(item, "best_offer", "bestOffer"),
                currency=str(item.get("currency") or "USD"),
                marketplace=str(
                    item.get("marketplace") or self.site.upper()),
                buyer_fees=_number(item, "buyer_fees", "buyerFees"),
                buyer_fee_rate=max(0.0, premium),
                minimum_buyer_fee=_number(
                    item, "minimum_buyer_fee", "minimumBuyerFee"),
                international_shipping=_number(
                    item, "international_shipping",
                    "internationalShipping"),
                insurance_rate=_number(
                    item, "insurance_rate", "insuranceRate"),
                insurance_on_buyer_fee=_boolean(
                    item, "insurance_on_buyer_fee",
                    "insuranceOnBuyerFee"),
                import_duty_rate=_number(
                    item, "import_duty_rate", "importDutyRate"),
                fx_spread_rate=_number(
                    item, "fx_spread_rate", "fxSpreadRate"),
            ))
            if len(output) >= max_results:
                break
        return output

    def _search(self, query: str, listing_type: str,
                max_results: int) -> list[Listing]:
        payload = self._load_payload(query, listing_type, max_results)
        if payload is None:
            return []
        return self._parse(payload, query, listing_type, max_results)

    def search_auctions(self, query: str,
                        max_results: int = 50) -> list[Listing]:
        return self._search(query, "auction", max_results)

    def search_fixed(self, query: str,
                     max_results: int = 50) -> list[Listing]:
        return self._search(query, "fixed", max_results)
