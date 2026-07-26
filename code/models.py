"""Shared data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Listing:
    """A live auction listing."""
    site: str
    title: str
    url: str
    current_price: float          # current bid or price, item only
    shipping: float = 0.0
    bid_count: int = 0
    end_time: Optional[datetime] = None
    image_url: str = ""
    listing_id: str = ""
    query: str = ""               # watchlist query that found it
    priority: bool = False        # from watchlist entry's priority flag
    discovery: bool = False       # broad theme query: browse, don't trust EV
    misspell_from: str = ""       # typo'd search that surfaced this listing
    listing_type: str = "auction"  # "auction" or "fixed" (BIN)
    best_offer: bool = False
    has_buy_now: bool = False     # auction that ALSO offers buy-it-now (hybrid)
    # For a hybrid, current_price is the BID and this is the price you can
    # actually transact at right now. With zero bids the bid is just the
    # seller's opening ask, so the BIN is the only real number on the page.
    buy_now_price: float = 0.0
    grail: str = ""               # matched personal-collection grail name
    grail_score: float = 0.0      # significance to the collection (40-100)
    created_at: Optional[datetime] = None   # when the listing went live
    currency: str = "USD"         # original currency (prices stored in USD)
    marketplace: str = "EBAY_US"
    seller_feedback: Optional[int] = None
    # Intended exit marketplace. ``auto`` lets the economics layer choose
    # the eligible venue with the highest proceeds; a watchlist entry can
    # still pin a specific channel (for example ``resale_channel: ebay``).
    resale_channel: str = "auto"
    category: str = ""
    # Landed-cost components. ``shipping`` is seller/domestic shipping.
    # The remaining fixed and percentage components make international
    # purchases explicit instead of hiding a proxy fee in the Ship column.
    buyer_fees: float = 0.0
    buyer_fee_rate: float = 0.0
    minimum_buyer_fee: float = 0.0
    international_shipping: float = 0.0
    insurance_rate: float = 0.0
    import_duty_rate: float = 0.0
    fx_spread_rate: float = 0.0

    @property
    def age_hours(self) -> Optional[float]:
        if not self.created_at:
            return None
        delta = datetime.now(timezone.utc) - self.created_at
        return max(0.0, delta.total_seconds() / 3600)

    @property
    def total_cost_now(self) -> float:
        return self.landed_cost(self.current_price)

    @property
    def fixed_acquisition_cost(self) -> float:
        return max(0.0, self.shipping or 0.0) + max(
            0.0, self.buyer_fees or 0.0) + max(
            0.0, self.international_shipping or 0.0)

    @property
    def variable_acquisition_rate(self) -> float:
        return sum(max(0.0, rate or 0.0) for rate in (
            self.insurance_rate, self.import_duty_rate,
            self.fx_spread_rate))

    def buyer_fee(self, item_price: float | None = None) -> float:
        """Percentage buyer premium, respecting a venue minimum."""
        price = self.current_price if item_price is None else item_price
        rate_fee = max(0.0, price or 0.0) * max(
            0.0, self.buyer_fee_rate or 0.0)
        if rate_fee <= 0 and not self.minimum_buyer_fee:
            return 0.0
        return max(rate_fee, max(0.0, self.minimum_buyer_fee or 0.0))

    def landed_cost(self, item_price: float | None = None) -> float:
        """Price plus every known fee required to get the item in hand."""
        price = self.current_price if item_price is None else item_price
        return (max(0.0, price or 0.0)
                * (1 + self.variable_acquisition_rate)
                + self.fixed_acquisition_cost + self.buyer_fee(price))

    def item_price_for_landed_cost(self, landed_cost: float) -> float:
        """Inverse of ``landed_cost`` for bid/offer ceilings."""
        available = landed_cost - self.fixed_acquisition_cost
        other_rate = self.variable_acquisition_rate
        buyer_rate = max(0.0, self.buyer_fee_rate or 0.0)
        minimum = max(0.0, self.minimum_buyer_fee or 0.0)
        # Try the percentage-premium branch first. If its calculated fee
        # clears the minimum, it is the exact inverse.
        price = available / (1 + other_rate + buyer_rate)
        if price * buyer_rate >= minimum:
            return max(0.0, price)
        # Below the crossover the buyer premium is a fixed minimum.
        return max(0.0, (available - minimum) / (1 + other_rate))

    def landed_cost_note(self, item_price: float | None = None) -> str:
        """Compact audit text for reports/logs when extras are non-zero."""
        price = self.current_price if item_price is None else item_price
        parts = []
        if self.shipping:
            parts.append(f"ship ${self.shipping:,.0f}")
        if self.buyer_fees:
            parts.append(f"buyer/proxy ${self.buyer_fees:,.0f}")
        if self.buyer_fee_rate:
            premium = f"buyer premium {self.buyer_fee_rate:.0%}"
            if self.minimum_buyer_fee:
                premium += f" (${self.minimum_buyer_fee:,.0f} min)"
            parts.append(premium)
        if self.international_shipping:
            parts.append(f"intl ship ${self.international_shipping:,.0f}")
        if self.import_duty_rate:
            parts.append(f"duty {self.import_duty_rate:.0%}")
        if self.fx_spread_rate:
            parts.append(f"FX {self.fx_spread_rate:.0%}")
        if self.insurance_rate:
            parts.append(f"insurance {self.insurance_rate:.0%}")
        if not parts:
            return ""
        return (f"landed ${self.landed_cost(price):,.0f} ("
                + ", ".join(parts) + ")")

    @property
    def hours_remaining(self) -> Optional[float]:
        if not self.end_time:
            return None
        delta = self.end_time - datetime.now(timezone.utc)
        return max(0.0, delta.total_seconds() / 3600)


@dataclass
class SoldComp:
    """A completed/sold sale used as a comparable."""
    title: str
    price: float                  # sold price incl. shipping if known
    sold_date: Optional[datetime] = None
    url: str = ""
    site: str = "ebay"


@dataclass
class Valuation:
    fair_value: float = 0.0
    comps_value: Optional[float] = None
    guide_value: Optional[float] = None
    n_comps: int = 0
    dispersion: float = 0.0       # MAD/median of comps (0 = tight)
    confidence: float = 0.0       # 0..1
    expected_cost: float = 0.0    # expected all-in acquisition cost
    expected_value: float = 0.0   # expected net profit if won
    edge_now: float = 0.0         # $ edge if won at the CURRENT bid
    capture: float = 0.0          # 0..1: how capturable that edge is (time decay)
    trend_30d: Optional[float] = None  # fair value pct change vs ~30d ago
    roi: float = 0.0
    sales_per_month: Optional[float] = None  # comp velocity (liquidity)
    annualized_roi: Optional[float] = None   # roi x turnover (capital velocity)
    opportunity_score: float = 0.0
    resale_channel: str = ""
    resale_fee_rate: float = 0.0
    net_proceeds: float = 0.0
    exit_advantage: float = 0.0
    # True when the listing's grade differed from its query's and fair
    # value was recomputed at the LISTING's grade (raw = assumed PSA 5).
    # Regraded fair values are per-listing - excluded from the query's
    # fair_history / trend tracking.
    regraded: bool = False
    # True when comps and guide disagree >4x - fair value is untrustworthy
    # (mongrel comp pool or wrong guide product). Confidence capped at 0.30;
    # kept out of fair_history so bad blends don't poison trends.
    disputed: bool = False
    notes: list[str] = field(default_factory=list)        # human/decision
    audit_notes: list[str] = field(default_factory=list)  # model diagnostics


@dataclass
class Opportunity:
    listing: Listing
    valuation: Valuation
