"""Shared acquisition and resale economics.

Keeping this math in one place prevents the valuation engine and the Excel
max-bid calculator from quietly using different definitions of "all in".
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from models import Listing


DEFAULT_RESALE_FEES = {
    "ebay": 0.1325,
    "psa_vault": 0.07,
    "goldin": 0.083,
    "heritage": 0.20,
    "fanatics_collect": 0.15,
}


@dataclass(frozen=True)
class ExitRoute:
    """One eligible sell-side route evaluated at a specific resale value."""
    channel: str
    fee_rate: float
    fixed_cost: float
    net_proceeds: float
    advantage_vs_ebay: float = 0.0


def _channel_config(config: dict, channel: str):
    algo = config.get("algorithm", {}) or {}
    configured = algo.get("resale_channels") or {}
    return configured.get(channel)


def _fee_rate_at(value, resale_value: float, fallback: float) -> float:
    if isinstance(value, dict):
        tiers = value.get("fee_tiers") or []
        for tier in tiers:
            try:
                if isinstance(tier, dict):
                    cap, rate = tier.get("up_to"), tier.get("fee_rate")
                else:
                    cap, rate = tier[0], tier[1]
                if cap is None or resale_value <= float(cap):
                    return max(0.0, min(float(rate), 0.95))
            except (TypeError, ValueError, IndexError):
                continue
        value = value.get("fee_rate")
    try:
        return max(0.0, min(float(value), 0.95))
    except (TypeError, ValueError):
        return fallback


def resale_fee_rate(config: dict, listing: Listing | None = None,
                    channel: str | None = None) -> float:
    """Configured all-in sell-side fee for the intended resale channel.

    Watchlist entries can set ``resale_channel``. Existing entries remain
    eBay by default and legacy ``algorithm.resale_fee_rate`` remains the
    fallback, so this is backwards compatible.
    """
    algo = config.get("algorithm", {}) or {}
    selected = str(channel or getattr(listing, "resale_channel", "")
                   or algo.get("default_resale_channel", "auto")).lower()
    if selected == "auto":
        selected = "ebay"
    configured = algo.get("resale_channels") or {}
    value = configured.get(selected)
    if value is None:
        if selected == "ebay":
            value = algo.get("resale_fee_rate",
                             DEFAULT_RESALE_FEES["ebay"])
        else:
            value = DEFAULT_RESALE_FEES.get(
                selected, algo.get("resale_fee_rate",
                                   DEFAULT_RESALE_FEES["ebay"]))
    return _fee_rate_at(
        value, 0.0, DEFAULT_RESALE_FEES.get(
            selected, DEFAULT_RESALE_FEES["ebay"]))


def _route_eligible(listing: Listing, resale_value: float,
                    route_cfg: dict) -> bool:
    if route_cfg.get("enabled", True) is False:
        return False
    if route_cfg.get("auto_enabled", True) is False:
        return False
    try:
        if resale_value < float(route_cfg.get("min_value", 0)):
            return False
        maximum = route_cfg.get("max_value")
        if maximum is not None and resale_value > float(maximum):
            return False
    except (TypeError, ValueError):
        return False
    categories = {str(c).lower() for c in
                  (route_cfg.get("categories") or [])}
    if categories and (listing.category or "").lower() not in categories:
        return False
    if route_cfg.get("requires_graded") and not re.search(
            r"\b(?:PSA|BGS|CGC|SGC|BVG|WATA|VGA)\b",
            listing.title or "", re.I):
        return False
    return True


def _evaluate_route(config: dict, channel: str,
                    resale_value: float) -> ExitRoute:
    route_cfg = _channel_config(config, channel)
    fallback = DEFAULT_RESALE_FEES.get(
        channel, DEFAULT_RESALE_FEES["ebay"])
    rate = _fee_rate_at(route_cfg, resale_value, fallback)
    fixed = 0.0
    if isinstance(route_cfg, dict):
        try:
            fixed = max(0.0, float(route_cfg.get("fixed_cost", 0)))
        except (TypeError, ValueError):
            pass
    net = max(0.0, resale_value * (1 - rate) - fixed)
    return ExitRoute(channel, rate, fixed, net)


def best_exit_route(config: dict, listing: Listing, resale_value: float,
                    allow_vault: bool = False) -> ExitRoute:
    """Choose the eligible exit with the highest expected net proceeds.

    A non-``auto`` listing channel is a deliberate watchlist override and
    is honored. Auto mode only considers channels present in configuration,
    keeping legacy configurations eBay-only.
    """
    value = max(0.0, float(resale_value or 0.0))
    algo = config.get("algorithm", {}) or {}
    selected = str(getattr(listing, "resale_channel", "") or
                   algo.get("default_resale_channel", "auto")).lower()
    if selected != "auto":
        chosen = _evaluate_route(config, selected, value)
        ebay = _evaluate_route(config, "ebay", value)
        return ExitRoute(chosen.channel, chosen.fee_rate, chosen.fixed_cost,
                         chosen.net_proceeds,
                         chosen.net_proceeds - ebay.net_proceeds)

    configured = algo.get("resale_channels") or {}
    channels = list(configured) if configured else ["ebay"]
    if "ebay" not in channels:
        channels.insert(0, "ebay")
    candidates = []
    for channel in channels:
        channel = str(channel).lower()
        if channel == "psa_vault":
            continue
        cfg = configured.get(channel)
        if isinstance(cfg, dict) and not _route_eligible(
                listing, value, cfg):
            continue
        candidates.append(_evaluate_route(config, channel, value))

    if allow_vault:
        vault = algo.get("psa_vault") or {}
        if vault.get("enabled", False):
            vault_cfg = {
                "fee_rate": vault.get("sell_fee_rate", 0.07),
                "fixed_cost": vault.get("fixed_cost", 0),
            }
            shadow = {"algorithm": {
                "resale_channels": {"psa_vault": vault_cfg}}}
            # The 0%-tax acquisition assumes the item stays in the vault.
            # Once that route is used, ordinary marketplace exits are not
            # interchangeable without taking delivery and changing the
            # tax/cost model, so the vault route is deliberately exclusive.
            candidates = [_evaluate_route(shadow, "psa_vault", value)]

    if not candidates:
        candidates = [_evaluate_route(config, "ebay", value)]
    chosen = max(candidates, key=lambda route: route.net_proceeds)
    ebay = _evaluate_route(config, "ebay", value)
    return ExitRoute(chosen.channel, chosen.fee_rate, chosen.fixed_cost,
                     chosen.net_proceeds,
                     chosen.net_proceeds - ebay.net_proceeds)


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
