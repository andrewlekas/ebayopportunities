"""Generic authorized JSON/CSV feed described by a source manifest."""
from __future__ import annotations

import os

from .authorized_feed import AuthorizedFeedScraper


def _nested(item: dict, dotted: str):
    value = item
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _set_default(item: dict, key: str, value) -> None:
    if value not in (None, "") and item.get(key) in (None, ""):
        item[key] = value


class ManifestFeedScraper(AuthorizedFeedScraper):
    def __init__(self, config: dict, manifest):
        self.manifest = manifest
        self.site = manifest.source_id
        self.credentials_key = manifest.source_id
        self.display_name = manifest.display_name
        self.capabilities = manifest.capabilities
        economics = manifest.economics
        self.default_auction_premium = float(
            economics.get(
                "auction_buyer_fee_rate",
                economics.get("buyer_fee_rate", 0)) or 0)
        super().__init__(config)

        credentials = (
            (config.get("api_keys") or {}).get(self.credentials_key) or {})
        access = manifest.access
        self.authorized = bool(
            credentials.get("authorized",
                            access.get("authorized", False)))
        self.endpoint = str(
            credentials.get("endpoint")
            or access.get("endpoint") or "").strip()
        token_env = str(access.get("access_token_env") or "").strip()
        key_env = str(access.get("api_key_env") or "").strip()
        self.access_token = str(
            credentials.get("access_token")
            or (os.environ.get(token_env) if token_env else "")
            or "").strip()
        self.api_key = str(
            credentials.get("api_key")
            or (os.environ.get(key_env) if key_env else "")
            or "").strip()
        self.api_key_header = str(
            credentials.get("api_key_header")
            or access.get("api_key_header") or "X-API-Key").strip()
        feed_file = str(
            credentials.get("feed_file")
            or access.get("feed_file") or "").strip()
        if feed_file and not os.path.isabs(feed_file):
            feed_file = os.path.join(
                config.get("_config_dir") or os.getcwd(), feed_file)
        self.feed_file = feed_file
        self.field_map = manifest.field_map
        self.economics = economics

    def _normalize(self, source: dict, requested_type: str) -> dict:
        item = dict(source)
        for canonical, dotted in self.field_map.items():
            value = _nested(source, dotted)
            if value not in (None, ""):
                item[canonical] = value
        rate = self.economics.get(
            f"{requested_type}_buyer_fee_rate",
            self.economics.get("buyer_fee_rate"))
        _set_default(item, "buyer_fee_rate", rate)
        for key in (
                "minimum_buyer_fee", "shipping", "insurance_rate",
                "insurance_on_buyer_fee", "international_shipping",
                "import_duty_rate", "fx_spread_rate", "marketplace"):
            _set_default(item, key, self.economics.get(key))
        _set_default(item, "marketplace", self.site.upper())
        _set_default(item, "status", "live")
        _set_default(item, "listing_type", requested_type)
        return item

    def _parse(self, payload, query: str, requested_type: str = "auction",
               max_results: int = 50):
        items = self._items(payload)
        if items is None:
            return super()._parse(
                payload, query, requested_type, max_results)
        normalized = [
            self._normalize(item, requested_type)
            for item in items if isinstance(item, dict)]
        return super()._parse(
            {"items": normalized}, query, requested_type, max_results)
