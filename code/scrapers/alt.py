"""ALT authorized API/export connector."""
from __future__ import annotations

from .authorized_feed import AuthorizedFeedScraper


class AltScraper(AuthorizedFeedScraper):
    site = "alt"
    credentials_key = "alt"
    display_name = "ALT"
    default_auction_premium = 0.20
    capabilities = frozenset({"auctions", "fixed"})
