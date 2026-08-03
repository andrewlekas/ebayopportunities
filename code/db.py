"""SQLite history: comp cache, fair-value trends, calibration data.

Everything the scanner learns is persisted here so that (a) frequent BIN
sweeps can reuse recent comps instead of re-scraping, (b) fair values build
a 30-day trend line, and (c) predicted vs actual auction closes accumulate
for calibrating the model (run `python main.py --calibrate`).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from models import Listing, SoldComp, Valuation

ITEM_ID_RE = re.compile(r"/itm/(?:[^/]*/)?(\d{9,15})")
ITEM_PARAM_RE = re.compile(r"(?:[?&](?:item|itemid)=)(\d{9,15})", re.I)
log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS comps(
    query TEXT, title TEXT, price REAL, sold_date TEXT, url TEXT, site TEXT,
    scanned_at TEXT, comp_key TEXT, UNIQUE(query, url, price));
CREATE TABLE IF NOT EXISTS fair_history(
    query TEXT, ts TEXT, fair REAL, n_comps INTEGER, trusted INTEGER);
CREATE TABLE IF NOT EXISTS observations(
    item_id TEXT, site TEXT, query TEXT, title TEXT, listing_type TEXT,
    price REAL, shipping REAL, bids INTEGER, end_time TEXT,
    fair REAL, predicted_settle REAL, hours_left REAL, observed_at TEXT,
    n_comps INTEGER, confidence REAL, trusted INTEGER);
CREATE TABLE IF NOT EXISTS closed(
    item_id TEXT PRIMARY KEY, actual_price REAL, closed_at TEXT);
CREATE TABLE IF NOT EXISTS alerts(
    item_key TEXT PRIMARY KEY, alerted_at TEXT);
CREATE TABLE IF NOT EXISTS source_health(
    source TEXT, run_at TEXT, mode TEXT, status TEXT,
    ok INTEGER, failed INTEGER, skipped INTEGER,
    freshness_hours REAL, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_comps_q ON comps(query, scanned_at);
CREATE INDEX IF NOT EXISTS idx_obs_item ON observations(item_id);
CREATE INDEX IF NOT EXISTS idx_source_health_run
    ON source_health(run_at, source);
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
# trusted=1 attests that the row passed quality.evidence_rejection. Legacy
# rows migrate as NULL and stay available for audit, but learner.py cannot
# silently treat them as if today's safeguards had approved them.
OBS_ADDED_COLS = (("n_comps", "INTEGER"), ("confidence", "REAL"),
                  ("trusted", "INTEGER"))
FAIR_ADDED_COLS = (("trusted", "INTEGER"),)


def canonical_item_id(url: str, site: str = "") -> str:
    """Stable marketplace item id extracted from common listing URL forms."""
    text = str(url or "")
    match = ITEM_ID_RE.search(text) or ITEM_PARAM_RE.search(text)
    if match:
        return match.group(1)
    # eBay Browse and redirect URLs occasionally omit /itm/ but still carry
    # the 9-15 digit item id as a path segment.  Limit this fallback to eBay
    # so dates/order numbers on other marketplaces are not mistaken for ids.
    if str(site).lower() == "ebay":
        try:
            path = urlsplit(text).path
        except ValueError:
            path = text
        match = re.search(r"(?<!\d)(\d{9,15})(?!\d)", path)
        if match:
            return match.group(1)
    return ""


def _normalized_url(url: str) -> str:
    """Remove tracking/fragment noise while preserving listing identity."""
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        ignored = {"hash", "var", "mkcid", "mkevt", "mkrid", "campid",
                   "customid", "toolid"}
        query = [(k, v) for k, v in parse_qsl(parts.query,
                                              keep_blank_values=True)
                 if not k.lower().startswith("utm_")
                 and k.lower() not in ignored]
        return urlunsplit((
            parts.scheme.lower(), parts.netloc.lower(),
            parts.path.rstrip("/"), urlencode(sorted(query)), ""))
    except ValueError:
        return text.split("#", 1)[0].rstrip("/")


def canonical_comp_key(*, title: str, price: float,
                       sold_date, url: str, site: str) -> str:
    """Identity used to count a physical sold listing once per query."""
    item_id = canonical_item_id(url, site)
    if item_id:
        return f"item:{item_id}"
    normalized = _normalized_url(url)
    if normalized:
        return f"url:{normalized}"
    sold = sold_date.isoformat() if hasattr(sold_date, "isoformat") \
        else str(sold_date or "")
    raw = "\x1f".join((
        str(site or "").strip().lower(),
        " ".join(str(title or "").lower().split()),
        f"{float(price or 0):.2f}",
        sold,
    ))
    return "fallback:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _database_path(conn) -> str:
    try:
        for _, name, path in conn.execute("PRAGMA database_list"):
            if name == "main":
                return path or ""
    except sqlite3.Error:
        pass
    return ""


def _backup_before_comp_dedupe(conn) -> str:
    """Create a consistent SQLite backup before deleting duplicate rows."""
    path = _database_path(conn)
    if not path or path == ":memory:":
        return ""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = f"{path}-pre-comp-dedupe-{stamp}"
    suffix = 1
    while os.path.exists(backup):
        backup = f"{path}-pre-comp-dedupe-{stamp}-{suffix}"
        suffix += 1
    target = sqlite3.connect(backup)
    try:
        conn.backup(target)
    finally:
        target.close()
    return backup


def _backup_before_fair_quarantine(conn) -> str:
    """Back up legacy fair history before marking it untrusted."""
    path = _database_path(conn)
    if not path or path == ":memory:":
        return ""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = f"{path}-pre-fair-trust-{stamp}"
    suffix = 1
    while os.path.exists(backup):
        backup = f"{path}-pre-fair-trust-{stamp}-{suffix}"
        suffix += 1
    target = sqlite3.connect(backup)
    try:
        conn.backup(target)
    finally:
        target.close()
    return backup


def _migrate_comps(conn) -> dict:
    """Populate canonical comp keys, back up, and collapse old duplicates."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(comps)")}
    select = conn.execute(
        "SELECT rowid, query, title, price, sold_date, url, site FROM comps"
    ).fetchall()
    keyed = [
        (canonical_comp_key(title=title, price=price, sold_date=sold,
                            url=url, site=site), rowid, query, site)
        for rowid, query, title, price, sold, url, site in select
    ]
    identities: dict[tuple[str, str, str], int] = {}
    duplicate_rows = 0
    for key, _, query, site in keyed:
        identity = (query or "", site or "", key)
        duplicate_rows += int(identity in identities)
        identities[identity] = identities.get(identity, 0) + 1

    backup = _backup_before_comp_dedupe(conn) if duplicate_rows else ""
    if "comp_key" not in columns:
        conn.execute("ALTER TABLE comps ADD COLUMN comp_key TEXT")
    conn.executemany("UPDATE comps SET comp_key=? WHERE rowid=?",
                     [(key, rowid) for key, rowid, _, _ in keyed])
    if duplicate_rows:
        conn.execute("""
            DELETE FROM comps
            WHERE rowid NOT IN (
                SELECT MAX(rowid) FROM comps
                GROUP BY query, site, comp_key
            )""")
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_comps_identity
        ON comps(query, site, comp_key)
        WHERE comp_key IS NOT NULL AND comp_key <> ''""")
    conn.commit()
    if duplicate_rows:
        log.warning("comp migration: removed %d duplicate rows; backup: %s",
                    duplicate_rows, backup or "(in-memory database)")
    return {"duplicates_removed": duplicate_rows, "backup": backup}


def _migrate(conn) -> None:
    """Additive schema migrations. Best-effort: a failure here must never
    stop a scan - record_observation adapts to whatever columns exist."""
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(observations)")}
        for name, decl in OBS_ADDED_COLS:
            if name not in have:
                conn.execute(
                    f"ALTER TABLE observations ADD COLUMN {name} {decl}")
        fair_have = {
            r[1] for r in conn.execute("PRAGMA table_info(fair_history)")}
        if any(name not in fair_have for name, _ in FAIR_ADDED_COLS):
            legacy_count = conn.execute(
                "SELECT COUNT(*) FROM fair_history").fetchone()[0]
            backup = (_backup_before_fair_quarantine(conn)
                      if legacy_count else "")
            for name, decl in FAIR_ADDED_COLS:
                if name not in fair_have:
                    conn.execute(
                        f"ALTER TABLE fair_history ADD COLUMN {name} {decl}")
            if legacy_count:
                log.warning(
                    "fair-history migration: quarantined %d legacy rows "
                    "(trusted=NULL); backup: %s", legacy_count,
                    backup or "(in-memory database)")
        _migrate_comps(conn)
        conn.commit()
    except (OSError, sqlite3.Error, ValueError) as exc:
        # Preserve the scanner's old best-effort migration behavior, but make
        # failure visible instead of silently hiding a data-integrity issue.
        conn.rollback()
        log.warning("database migration skipped: %s", exc)


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
    # A scraper can surface the same eBay sale through several URL shapes
    # (title path, short /itm/ path, tracking params).  Collapse the batch
    # before touching SQLite, then update the existing physical sale rather
    # than counting it again at a different price or scan time.
    unique: dict[tuple[str, str], tuple] = {}
    scanned_at = _now()
    for c in comps:
        key = canonical_comp_key(
            title=c.title, price=c.price, sold_date=c.sold_date,
            url=c.url, site=c.site)
        unique[(c.site or "", key)] = (
            query, c.title, c.price,
            c.sold_date.isoformat() if c.sold_date else None,
            c.url, c.site, scanned_at, key)
    try:
        for row in unique.values():
            cur = conn.execute(
                "UPDATE comps SET title=?, price=?, sold_date=?, url=?, "
                "scanned_at=? WHERE query=? AND site=? AND comp_key=?",
                (row[1], row[2], row[3], row[4], row[6],
                 row[0], row[5], row[7]))
            if not cur.rowcount:
                conn.execute(
                    "INSERT OR IGNORE INTO comps "
                    "(query,title,price,sold_date,url,site,scanned_at,comp_key) "
                    "VALUES (?,?,?,?,?,?,?,?)", row)
    except sqlite3.OperationalError:
        # Pre-migration fallback if a read-only/locked database prevented the
        # additive migration.  Scanning continues under the legacy identity.
        rows = [row[:7] for row in unique.values()]
        conn.executemany(
            "INSERT OR IGNORE INTO comps "
            "(query,title,price,sold_date,url,site,scanned_at) "
            "VALUES (?,?,?,?,?,?,?)", rows)
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


def comp_cache_age_hours(conn, query: str) -> float | None:
    """Hours since the newest cached comp for this query, or None if empty.

    `cached_comps(allow_stale=True)` deliberately returns old rows when the
    comp lane is blocked - stale beats none. But the caller then had no way
    to say HOW old, so a valuation built on week-old evidence looked exactly
    like one built on this morning's. This is what lets the row carry that
    fact into the report and out of the decision sheets.
    """
    row = conn.execute(
        "SELECT MAX(scanned_at) FROM comps WHERE query=?", (query,)).fetchone()
    if not row or not row[0]:
        return None
    try:
        seen = datetime.fromisoformat(row[0])
    except (TypeError, ValueError):
        return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - seen).total_seconds() / 3600.0
    return max(0.0, age)


# ---------------- fair-value trend ----------------
def record_fair(conn, query: str, fair: float, n_comps: int) -> None:
    conn.execute(
        "INSERT INTO fair_history "
        "(query,ts,fair,n_comps,trusted) VALUES (?,?,?,?,1)",
        (query, _now(), fair, n_comps))
    conn.commit()


def trend_30d(conn, query: str, current_fair: float) -> float | None:
    """Pct change vs the fair value recorded closest to 30 days ago."""
    target = datetime.now(timezone.utc) - timedelta(days=30)
    row = conn.execute(
        "SELECT fair FROM fair_history "
        "WHERE query=? AND trusted=1 AND ts<=? "
        "ORDER BY ts DESC LIMIT 1", (query, target.isoformat())).fetchone()
    if not row:  # fall back to oldest record if history is <30d (>=3d old)
        row = conn.execute(
            "SELECT fair, ts FROM fair_history "
            "WHERE query=? AND trusted=1 "
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


# ---------------- persistent source health ----------------
def record_source_health(conn, rows: list[dict]) -> None:
    """Append one readiness snapshot for each configured data source."""
    if not rows:
        return
    conn.executemany(
        "INSERT INTO source_health "
        "(source,run_at,mode,status,ok,failed,skipped,"
        "freshness_hours,detail) VALUES (?,?,?,?,?,?,?,?,?)",
        [(r.get("source", ""), r.get("run_at") or _now(),
          r.get("mode", ""), r.get("status", "unknown"),
          int(r.get("ok") or 0), int(r.get("failed") or 0),
          int(r.get("skipped") or 0), r.get("freshness_hours"),
          str(r.get("detail") or "")) for r in rows])
    conn.commit()


def latest_source_health(conn) -> list[dict]:
    """Most recent persisted row per source."""
    rows = conn.execute(
        """SELECT source,run_at,mode,status,ok,failed,skipped,
                  freshness_hours,detail
           FROM source_health AS h
           WHERE rowid IN (
               SELECT MAX(rowid) FROM source_health GROUP BY source)
           ORDER BY source""").fetchall()
    keys = ("source", "run_at", "mode", "status", "ok", "failed",
            "skipped", "freshness_hours", "detail")
    return [dict(zip(keys, row)) for row in rows]


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
        "trusted": 1,
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
