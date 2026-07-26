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

    @property
    def age_hours(self) -> Optional[float]:
        if not self.created_at:
            return None
        delta = datetime.now(timezone.utc) - self.created_at
        return max(0.0, delta.total_seconds() / 3600)

    @property
    def total_cost_now(self) -> float:
        return self.current_price + self.shipping

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
