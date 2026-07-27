"""Fanatics Collect authorized API/export connector.

No browser-discovered Algolia credentials are used here.  Configure a
Fanatics-approved endpoint or a normalized local export under
``api_keys.fanatics``.  The shared feed contract is documented in
docs/PLATFORM_ACCESS.md.
"""
from __future__ import annotations

from .authorized_feed import AuthorizedFeedScraper


class FanaticsCollectScraper(AuthorizedFeedScraper):
    site = "fanatics_collect"
    credentials_key = "fanatics"
    display_name = "Fanatics Collect"
    default_auction_premium = 0.20
    capabilities = frozenset({"auctions", "fixed"})
