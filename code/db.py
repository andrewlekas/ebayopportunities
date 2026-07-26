"""SQLite history: comp cache, fair-value trends, calibration data.

Everything the scanner learns is persisted here so that (a) frequent BIN
sweeps can reuse recent comps instead of re-scraping, (b) fair values build
a 30-day trend line, and (c) predicted vs actual auction closes accumulate
for calibrating the model (run `python main.py --calibrate`).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from models import Listing, SoldComp, Valuation

ITEM_ID_RE = re.compile(r"/itm/(?:[^/]*/)?(\d{9,15})")

SCHEMA = """
CREATE TABLE IF NOT EXISTS comps(
    query TEXT, title TEXT, price REAL, sold_date TEXT, url TEXT, site TEXT,
    scanned_at TEXT, UNIQUE(query, url, price));
CREATE TABLE IF NOT EXISTS fair_history(
    query TEXT, ts TEXT, fair REAL, n_comps INTEGER);
CREATE TABLE IF NOT EXISTS observations(
    item_id TEXT, site TEXT, query TEXT, title TEXT, listing_type TEXT,
    price REAL, shipping REAL, bids INTEGER, end_time TEXT,
    fair REAL, predicted_settle REAL, hours_left REAL, observed_at TEXT,
    n_comps INTEGER, confidence REAL);
CREATE TABLE IF NOT EXISTS closed(
    item_id TEXT PRIMARY KEY, actual_price REAL, closed_at TEXT);
CREATE TABLE IF NOT EXISTS alerts(
    item_key TEXT PRIMARY KEY, alerted_at TEXT);
CREATE INDEX IF NOT EXISTS idx_comps_q ON comps(query, scanned_at);
CREATE INDEX IF NOT EXISTS idx_obs_item ON observations(item_id);
"""


# observations: base columns (original schema) + columns added later.
# CREATE TABLE IF NOT EXISTS cannot add columns to an existing table, so
# anything new has to be ALTERed in - see _migrate().
OBS_BASE_COLS = ("item_id", "site", "query", "title", "listing_type",
                 "price", "shipping", "bids", "end_time", "fair",
                 "predicted_settle", "hours_left", "observed_at")
# n_comps/confidence record HOW TRUSTWORTHY the fair value was at the time.
# Without them the learner had no way to tell a well-comped $2,500 valuation
# from a mongrel $2.85 one, and trained on both equally (2026-07-25).
OBS_ADDED_COLS = (("n_comps", "INTEGER"), ("confidence", "REAL"))


def _migrate(conn) -> None:
    """Additive schema migrations. Best-effort: a failure here must never
    stop a scan - record_observation adapts to whatever columns exist."""
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(observations)")}
        for name, decl in OBS_ADDED_COLS:
            if name not in have:
                conn.execute(
                    f"ALTER TABLE observations ADD COLUMN {name} {decl}")
        conn.commit()
    except sqlite3.Error:
        pass


def connect(path: str = "history.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def observation_columns(conn) -> tuple[str, ...]:
    """Columns record_observation can actually write on this database."""
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(observations)")}
    except sqlite3.Error:
        return OBS_BASE_COLS
    return OBS_BASE_COLS + tuple(n for n, _ in OBS_ADDED_COLS if n in have)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------- comps ----------------
def save_comps(conn, query: str, comps: list[SoldComp]) -> None:
    rows = [(query, c.title, c.price,
             c.sold_date.isoformat() if c.sold_date else None,
             c.url, c.site, _now()) for c in comps]
    conn.executemany("INSERT OR IGNORE INTO comps VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()


def cached_comps(conn, query: str, max_age_hours: float = 24.0,
                 min_count: int = 5,
                 allow_stale: bool = False) -> list[SoldComp] | None:
    """Recent-enough comps for this query, or None if the cache is stale.

    allow_stale=True returns whatever exists regardless of age - used as a
    fallback when eBay is blocking fresh comp fetches (stale beats none;
    recency weighting already discounts old sales).
    """
    if not allow_stale:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=max_age_hours)).isoformat()
        fresh = conn.execute(
            "SELECT COUNT(*) FROM comps WHERE query=? AND scanned_at>=?",
            (query, cutoff)).fetchone()[0]
        if fresh < min_count:
            return None
    rows = conn.execute(
        "SELECT title, price, sold_date, url, site FROM comps WHERE query=? "
        "ORDER BY scanned_at DESC LIMIT 120", (query,)).fetchall()
    out = []
    for title, price, sold, url, site in rows:
        dt = datetime.fromisoformat(sold) if sold else None
        out.append(SoldComp(title=title, price=price, sold_date=dt,
                            url=url, site=site))
    return out


# ---------------- fair-value trend ----------------
def record_fair(conn, query: str, fair: float, n_comps: int) -> None:
    conn.execute("INSERT INTO fair_history VALUES (?,?,?,?)",
                 (query, _now(), fair, n_comps))
    conn.commit()


def trend_30d(conn, query: str, current_fair: float) -> float | None:
    """Pct change vs the fair value recorded closest to 30 days ago."""
    target = datetime.now(timezone.utc) - timedelta(days=30)
    row = conn.execute(
        "SELECT fair FROM fair_history WHERE query=? AND ts<=? "
        "ORDER BY ts DESC LIMIT 1", (query, target.isoformat())).fetchone()
    if not row:  # fall back to oldest record if history is <30d (>=3d old)
        row = conn.execute(
            "SELECT fair, ts FROM fair_history WHERE query=? "
            "ORDER BY ts ASC LIMIT 1", (query,)).fetchone()
        if not row or not row[0]:
            return None
        age = datetime.now(timezone.utc) - datetime.fromisoformat(row[1])
        if age < timedelta(days=3):
            return None
    old = row[0]
    if not old:
        return None
    return (current_fair - old) / old


# ---------------- calibration ----------------
def record_observation(conn, listing: Listing, v: Valuation) -> None:
    if not listing.listing_id or listing.listing_type != "auction":
        return
    m = ITEM_ID_RE.search(listing.url or "")
    item_id = m.group(1) if m else str(listing.listing_id)
    values = {
        "item_id": item_id, "site": listing.site, "query": listing.query,
        "title": listing.title, "listing_type": listing.listing_type,
        "price": listing.current_price, "shipping": listing.shipping,
        "bids": listing.bid_count,
        "end_time": listing.end_time.isoformat() if listing.end_time else None,
        "fair": v.fair_value, "predicted_settle": v.expected_cost,
        "hours_left": listing.hours_remaining, "observed_at": _now(),
        "n_comps": v.n_comps, "confidence": v.confidence,
    }
    cols = observation_columns(conn)
    conn.execute(
        "INSERT INTO observations (%s) VALUES (%s)"
        % (", ".join(cols), ", ".join("?" * len(cols))),
        tuple(values[c] for c in cols))
    conn.commit()


def match_closed(conn, comps: list[SoldComp]) -> int:
    """Match sold comps back to auctions we observed -> ground truth."""
    n = 0
    for c in comps:
        m = ITEM_ID_RE.search(c.url or "")
        if not m:
            continue
        item_id = m.group(1)
        seen = conn.execute("SELECT 1 FROM observations WHERE item_id=? LIMIT 1",
                            (item_id,)).fetchone()
        if seen:
            cur = conn.execute(
                "INSERT OR IGNORE INTO closed VALUES (?,?,?)",
                (item_id, c.price, _now()))
            n += cur.rowcount
    conn.commit()
    return n


def calibration_report(conn) -> str:
    rows = conn.execute("""
        SELECT o.hours_left, o.fair, o.predicted_settle, c.actual_price
        FROM observations o JOIN closed c ON o.item_id = c.item_id
        WHERE o.fair > 0 AND c.actual_price > 0""").fetchall()
    if len(rows) < 20:
        return (f"Only {len(rows)} matched auction closes so far - need ~20+ "
                "for a meaningful calibration. Keep scanning; matches "
                "accumulate automatically.")
    ratios = sorted(r[3] / r[1] for r in rows)
    settle = ratios[len(ratios) // 2]
    errs = [abs(r[3] - r[2]) / r[1] for r in rows if r[2]]
    mae = sum(errs) / len(errs) if errs else 0
    lines = [f"Matched closes: {len(rows)}",
             f"Median actual/fair ratio: {settle:.3f} "
             f"(config auction_settle_ratio is the knob)",
             f"Mean |predicted - actual| / fair: {mae:.1%}"]
    # bucket by hours remaining at observation
    for lo, hi in [(0, 6), (6, 24), (24, 72), (72, 100000)]:
        b = [r for r in rows if r[0] is not None and lo <= r[0] < hi]
        if len(b) >= 5:
            rs = sorted(x[3] / x[1] for x in b)
            lines.append(f"  {lo:>3}-{hi if hi < 99999 else '...'}h out: "
                         f"n={len(b)}, median close = {rs[len(rs)//2]:.0%} of fair")
    return "\n".join(lines)
