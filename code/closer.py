"""Close-tracker: fetch the ACTUAL final price of auctions we observed.

Calibration ground truth used to depend on scraped sold comps fuzzy-matched
by title - fragile, and completely starved whenever comp scraping is
blocked. This module instead looks up each observed auction BY ITEM ID
shortly after its end time and records the real close in the `closed`
table. Exact matches, no fuzzy titles, no dependence on the comps pipeline.

Runs at the end of every live scan/sweep (capped per run to stay polite).
Ended eBay item pages show "Winning bid: US $x" (or "Sold for") for months,
so a handful of lookups per 30-minute sweep keeps up easily.

actual_price recorded = winning bid + the shipping we observed (comps are
shipping-inclusive, so calibration ratios stay apples-to-apples).
Unsold/ended-without-sale items are recorded with actual_price = 0 so we
stop re-checking them; calibration queries filter actual_price > 0.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import db as histdb
from scrapers.ebay import EbayScraper

log = logging.getLogger(__name__)

PRICE_RES = [
    re.compile(r"winning\s+bid[^$]{0,60}\$\s*([\d,]+\.?\d*)", re.I),
    re.compile(r"sold\s+for[^$]{0,60}\$\s*([\d,]+\.?\d*)", re.I),
]
UNSOLD_RE = re.compile(
    r"item (?:was|went) unsold|listing (?:was )?ended by the seller|"
    r"bidding has ended[^$]{0,200}item (?:was|went) unsold", re.I)
ENDED_RE = re.compile(r"bidding has ended|this listing (?:has )?ended|"
                      r"item is no longer available", re.I)
CHALLENGE_MARKERS = ("pardon our interruption", "splashui/challenge",
                     "please verify yourself", "checking your browser")


def _final_price(ebay: EbayScraper, item_id: str) -> float | None:
    """Winning bid for an ended item, 0.0 if it went unsold,
    None if undetermined (retry next run)."""
    # OWN BREAKER LANE (2026-07-26). This used to share the "html" lane with
    # sold-comp search scraping, so every comp-scrape challenge starved close
    # tracking too - and close tracking is the ONLY source of ground truth
    # for the settle ratio. The symptom: "settled 0 real closes, 20 pending
    # retry" on every run for days, taking 0s because the breaker
    # short-circuited it before a single request went out. The learner sat at
    # n=0 as a direct result.
    #
    # The two activities also have different risk profiles: a single item
    # page is a normal browse, while repeated sold-listing SEARCH pages are
    # what actually attract challenges. They should not share a fuse. This
    # lane still trips on its own three failures and is capped at 20 lookups
    # per run, so it cannot become a hammer.
    r = ebay._get(f"https://www.ebay.com/itm/{item_id}", lane="close")
    if not r:
        return None
    head = r.text[:4000].lower()
    if any(m in head for m in CHALLENGE_MARKERS):
        ebay._streaks["close"] += 1
        log.warning("closer: bot-challenge page for item %s (%d/%d)",
                    item_id, ebay._streaks["close"], ebay.trip_after)
        ebay.note_challenge("close")
        return None
    text = r.text
    for rx in PRICE_RES:
        m = rx.search(text)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    if UNSOLD_RE.search(text):
        return 0.0
    if ENDED_RE.search(text):
        # ended but no winning-bid marker found: usually unsold or the
        # layout moved; count as unsold so we don't retry forever
        return 0.0
    return None


def settle_closes(config: dict) -> str:
    """Look up recently-ended observed auctions and record actual closes."""
    dbc = config.get("database", {})
    conn = histdb.connect(dbc.get("file", "history.db"))
    cap = config.get("scraping", {}).get("close_lookups_per_run", 20)
    now = datetime.now(timezone.utc)
    rows = conn.execute(
        """SELECT o.item_id, MAX(o.shipping)
           FROM observations o LEFT JOIN closed c ON c.item_id = o.item_id
           WHERE c.item_id IS NULL AND o.site = 'ebay'
                 AND o.end_time IS NOT NULL AND o.end_time < ?
                 AND o.end_time > ?
           GROUP BY o.item_id ORDER BY MAX(o.end_time) DESC LIMIT ?""",
        (now.isoformat(), (now - timedelta(days=10)).isoformat(),
         cap)).fetchall()
    if not rows:
        conn.close()
        return "closer: no ended auctions awaiting settlement"

    ebay = EbayScraper(config)
    n_ok = n_unsold = 0
    for item_id, shipping in rows:
        # `ebay.tripped` is the HTML-lane shorthand. Consulting it here undid
        # the whole point of giving close tracking its own fuse: with
        # sold-comp scraping cooling, this broke out of the loop before
        # _final_price was ever called - which is why the 18:00 run still
        # reported "settled 0 real closes" and made no ebay/close requests
        # at all. Ask the lane we actually use.
        if ebay.lane_tripped("close"):
            break
        price = _final_price(ebay, item_id)
        if price is None:
            continue                      # undetermined - retry next run
        actual = price + (shipping or 0.0) if price > 0 else 0.0
        conn.execute("INSERT OR IGNORE INTO closed VALUES (?,?,?)",
                     (item_id, actual,
                      datetime.now(timezone.utc).isoformat()))
        if price > 0:
            n_ok += 1
        else:
            n_unsold += 1
    conn.commit()
    conn.close()
    pending = len(rows) - n_ok - n_unsold
    return (f"closer: settled {n_ok} real closes, {n_unsold} unsold, "
            f"{pending} pending retry (of {len(rows)} ended)")
