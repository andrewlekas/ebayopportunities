"""Shared acquisition and resale economics.

Keeping this math in one place prevents the valuation engine and the Excel
max-bid calculator from quietly using different definitions of "all in".
"""
from __future__ import annotations

from models import Listing


DEFAULT_RESALE_FEES = {
    "ebay": 0.1325,
    "psa_vault": 0.07,
    "goldin": 0.20,
    "heritage": 0.20,
    "fanatics_collect": 0.15,
}


def resale_fee_rate(config: dict, listing: Listing | None = None,
                    channel: str | None = None) -> float:
    """Configured all-in sell-side fee for the intended resale channel.

    Watchlist entries can set ``resale_channel``. Existing entries remain
    eBay by default and legacy ``algorithm.resale_fee_rate`` remains the
    fallback, so this is backwards compatible.
    """
    algo = config.get("algorithm", {}) or {}
    selected = str(channel or getattr(listing, "resale_channel", "")
                   or algo.get("default_resale_channel", "ebay")).lower()
    configured = algo.get("resale_channels") or {}
    value = configured.get(selected)
    if isinstance(value, dict):
        value = value.get("fee_rate")
    if value is None:
        if selected == "ebay":
            value = algo.get("resale_fee_rate",
                             DEFAULT_RESALE_FEES["ebay"])
        else:
            value = DEFAULT_RESALE_FEES.get(
                selected, algo.get("resale_fee_rate",
                                   DEFAULT_RESALE_FEES["ebay"]))
    try:
        return max(0.0, min(float(value), 0.95))
    except (TypeError, ValueError):
        return DEFAULT_RESALE_FEES["ebay"]


def sales_tax_rate(config: dict, listing: Listing,
                   vault_route: bool = False) -> float:
    """Effective buy-side tax rate after marketplace/vault exemptions."""
    if vault_route:
        return 0.0
    algo = config.get("algorithm", {}) or {}
    exempt = {str(m).upper() for m in
              (algo.get("tax_free_marketplaces") or [])}
    if (listing.marketplace or "").upper() in exempt:
        return 0.0
    try:
        return max(0.0, float(algo.get("sales_tax_rate", 0.0)))
    except (TypeError, ValueError):
        return 0.0


def total_acquisition_cost(listing: Listing, item_price: float,
                           tax_rate: float = 0.0) -> float:
    """All-in landed acquisition cost, then checkout tax if applicable."""
    return listing.landed_cost(item_price) * (1 + max(0.0, tax_rate))


def item_price_for_total_cost(listing: Listing, total_cost: float,
                              tax_rate: float = 0.0) -> float:
    """Invert ``total_acquisition_cost`` to a maximum item bid/offer."""
    pre_tax = total_cost / (1 + max(0.0, tax_rate))
    return listing.item_price_for_landed_cost(pre_tax)
