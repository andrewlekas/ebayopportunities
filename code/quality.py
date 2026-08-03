"""Central evidence and actionability policy.

Category sheets may show uncertain rows for research, but uncertain
valuations must never teach the close model or appear as buy/bid decisions.
Keeping the rules here prevents Today, alerts, digest and portfolio marking
from slowly drifting into different definitions of "safe enough to act on".
"""
from __future__ import annotations

from models import Opportunity, Valuation


NOTE_BLOCKERS = (
    ("ASK-BASED", "ask-based valuation"),
    ("MIXED POOL", "mixed comp pool"),
    ("SUSPICIOUS", "suspicious listing"),
    ("BELOW DECISION FLOOR", "below decision value floor"),
    # 2026-07-26: a value built on a set-level pool is browsing information,
    # never a bid target - however confident its sources look. Eight Disney
    # parallels once shared one $1,069.60 with 84% comps/guide "agreement",
    # because both sources agreed at the WRONG level of specificity.
    ("IDENTITY UNRESOLVED", "card identity not resolved"),
    # 2026-08-02: when eBay opens the comp breaker the scan keeps running on
    # cached sold prices - correct, since stale beats none. But nothing
    # recorded that the evidence was frozen, so a row whose comps had not
    # moved in over a week reached Action looking exactly like one priced
    # from this morning's sales. Browsing on old comps is fine; bidding on
    # them is not. The threshold is database.stale_comp_block_hours, and
    # "AGING COMPS" is the softer marker that annotates without blocking.
    ("STALE COMPS", "comp evidence frozen by a blocked source"),
)


def note_blocker(v: Valuation) -> str | None:
    """First hard-risk marker attached by the valuation engine."""
    notes = " | ".join(str(note).upper() for note in v.notes)
    for marker, reason in NOTE_BLOCKERS:
        if marker in notes:
            return reason
    return None


def tradeability_rejection(o: Opportunity) -> str | None:
    """Why this row must stay out of every decision-oriented output."""
    l, v = o.listing, o.valuation
    if l.discovery:
        return "discovery query"
    if v.fair_value <= 0:
        return "no fair value"
    if v.disputed:
        return "disputed valuation"
    return note_blocker(v)


def is_tradeable(o: Opportunity) -> bool:
    return tradeability_rejection(o) is None


def evidence_rejection(o: Opportunity, *, collection_passed: bool,
                       min_fair: float = 50.0,
                       min_comps: int = 3) -> str | None:
    """Why a valuation cannot enter fair history or learner observations."""
    l, v = o.listing, o.valuation
    if l.discovery:
        return "discovery query"
    if not collection_passed:
        return "outside collection standards"
    if v.fair_value <= 0:
        return "no fair value"
    if v.fair_value < min_fair:
        return "fair value below trust floor"
    if v.n_comps < min_comps:
        return "too few matched comps"
    if v.regraded:
        return "listing-specific regrade"
    if v.disputed:
        return "disputed valuation"
    return note_blocker(v)
