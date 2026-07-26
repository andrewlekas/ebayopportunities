"""Synthetic listings + comps so the full pipeline runs without network/keys.

Run: python main.py --demo
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from models import Listing, SoldComp

random.seed(42)
NOW = datetime.now(timezone.utc)

# query -> (true market value, comp noise)
# These mirror what Andrew actually collects, so the demo exercises the
# real collecting standards: 1st Edition Pokemon, a numbered vintage sports
# card, and a sealed graded game. Anything else is filtered out by design.
MARKET = {
    "1999 1st Edition Base Set Charizard Holo PSA 8": (820.0, 0.10),
    "1948 Bowman #69 George Mikan RC PSA 4": (5200.0, 0.12),
    "Super Mario Bros 1985 NES Sealed WATA 9.4": (2400.0, 0.15),
}


def demo_comps(query: str) -> list[SoldComp]:
    base, noise = MARKET[query]
    comps = []
    for i in range(14):
        price = base * random.gauss(1.0, noise)
        # NB: no "#i" suffix - that would read as a card number and
        # conflict with the query's own number
        comps.append(SoldComp(
            title=f"{query} {'Mint ' if i % 3 else ''}sale {i}",
            price=round(max(price, 1), 2),
            sold_date=NOW - timedelta(days=random.uniform(1, 75)),
            url=f"https://www.ebay.com/itm/demo-sold-{i}", site="ebay"))
    # a shill/outlier sale and a wrong-grade sale (should be filtered)
    comps.append(SoldComp(title=query, price=base * 3.1, sold_date=NOW))
    comps.append(SoldComp(title=query.replace("PSA 8", "PSA 10").replace("PSA 10", "PSA 6"),
                          price=base * 0.3, sold_date=NOW))
    return comps


def demo_title(query: str, i: int) -> str:
    """A listing title for this query - keeps the era/condition markers the
    collecting standards look for."""
    return f"{query} - Auction {i + 1}"


def demo_listings(query: str) -> list[Listing]:
    base, _ = MARKET[query]
    sites = ["ebay", "ebay", "goldin", "fanatics_collect", "heritage"]
    out = []
    for i, site in enumerate(sites):
        frac = random.uniform(0.45, 1.1)          # some bargains, some rich
        hrs = random.uniform(0.5, 96)
        out.append(Listing(
            site=site,
            title=demo_title(query, i),
            url=f"https://{site}.example/item/{i}",
            current_price=round(base * frac, 2),
            shipping=round(random.uniform(0, 15), 2),
            bid_count=random.randint(0, 24),
            end_time=NOW + timedelta(hours=hrs),
            query=query))
    return out


def demo_guide_value(query: str) -> float:
    base, _ = MARKET[query]
    return round(base * random.gauss(1.0, 0.05), 2)
