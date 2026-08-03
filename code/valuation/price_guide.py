"""Price-guide reference values.

Sources (both optional, keyed via config):
  - PriceCharting API: great for graded Pokemon cards (has PSA/BGS/CGC prices)
  - pokemontcg.io: TCGPlayer market price for raw (ungraded) cards
Returns None when no source is available/matches - the blender handles it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

import paths
from scrapers.base import note_api
from security import redact_text

from . import comps as comps_mod
from .comps import title_match_score, grade_info, GRADE_RE
from .identity import (CardIdentity, MATCH_EXACT, MATCH_NONE, MATCH_STRONG,
                       MATCH_WEAK, genre_class, match_band)

log = logging.getLogger(__name__)

# PriceCharting reuses its game-condition field names for card grades.
# Ladder in PSA-EQUIVALENT terms - a CGC/BGS/SGC card must be looked up at
# its shifted grade (a CGC 10 is a PSA 9), which is what grade_info's third
# element gives us. Before 2026-07-25 this used the RAW grade, so every
# non-PSA slab was priced one full grade too high.
GUIDE_GRADE_LADDER = [
    (7.0, "cib-price"),
    (8.0, "new-price"),
    (9.0, "graded-price"),
    (9.5, "box-only-price"),
    (10.0, "manual-only-price"),
]
RAW_FIELD = "loose-price"
MIN_GRADED_RUNG = GUIDE_GRADE_LADDER[0][0]

# Categories no card price guide may value, from any source - API or local
# CSV. A signed photo, a match-worn shirt or a pin flag is not the card it
# depicts, however exactly its title matches one. These rows are valued
# from comps or not at all.
NO_CARD_GUIDE = frozenset({"Sports Memorabilia"})

# PriceCharting publishes a real market price for the top slab of each
# grading company.  Until 2026-07-26 these were unused: a CGC 10 was shifted
# down to PSA 9 by Andrew's cross-grader rule and read `graded-price`.  That
# rule exists to ESTIMATE the CGC discount - but when PriceCharting already
# observes the CGC 10 market directly, the observation beats the estimate.
# Fall back to the shift whenever the grader-specific field is absent.
GRADER_TOP_FIELD = {
    "cgc": "condition-17-price",   # CGC 10
    "sgc": "condition-18-price",   # SGC 10
    "bgs": "bgs-10-price",         # BGS 10
}
# Bump this whenever the grade->price rule changes, so cached values priced
# under the old rule are dropped instead of being served for the whole TTL.
GUIDE_CACHE_VERSION = "2026-07-28-sports-identity-v3"


def _pricecharting_product_key(query: str) -> str:
    """Canonical grade-free query for PriceCharting's product response.

    `/api/product` returns every grade price in one payload.  PSA 8, PSA 9,
    and equivalent CGC/BGS queries therefore share a single product fetch.
    Leading zeroes in card numbers are normalized because #015 and #15 are
    the same card and PriceCharting returns the same product for both.
    """
    value = re.sub(GRADE_RE, " ", query)
    value = re.sub(r"#0+(\d+)\b", r"#\1", value)
    return " ".join(value.casefold().split())


def _guide_cache_key(query: str) -> str:
    """Canonical final-value key: product identity + PSA-equivalent grade."""
    gi = grade_info(query)
    grade = f"{float(gi[2]):g}" if gi else "raw"
    return f"{_pricecharting_product_key(query)}|psa:{grade}"


DEFAULT_GUIDE_HOSTS = [
    "https://www.pricecharting.com",     # TCG, video games, comics, Funko
    "https://www.sportscardspro.com",    # sports cards - same company/token
]


def _host_tag(host: str) -> str:
    """Short cache-key suffix so two hosts never share a cached answer."""
    return host.split("//")[-1].split(".")[1] if "." in host else host


def candidates_of(listing: dict) -> list:
    """Every product PriceCharting returned for a search.

    The docs say /api/products returns "the first 20 products matching your
    search"; in practice it returns up to 100. Truncating to 20 before
    scoring meant the correct product could be discarded unseen purely
    because of where PriceCharting's own relevance ranking put it - a Base
    Set Charizard sat outside the first 20 while worse matches were scored.
    Scoring is local and cheap; score them all.
    """
    return listing.get("products") or []


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _retry_after_seconds(response, default: float = 5.0) -> float:
    """Parse Retry-After seconds or HTTP-date, with conservative bounds."""
    raw = ((getattr(response, "headers", None) or {})
           .get("Retry-After", ""))
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(raw))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            seconds = (target - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = default
    return min(max(seconds, 1.0), 3600.0)


def _field_cents(product: dict, field: str) -> float | None:
    val = product.get(field)
    if not val:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _guide_cents(product: dict, eff_grade: float | None,
                 grader: str | None = None,
                 printed_grade: float | None = None
                 ) -> tuple[float | None, str]:
    """(cents, explanation) for a PSA-equivalent grade.

    When the slab is a CGC/SGC/BGS 10 and PriceCharting publishes that
    company's own top-grade price, use it directly: it is an observed market
    for this exact slab, so applying the cross-grader shift on top would
    double-count the discount.

    A grade is NEVER rounded up. The available price points are treated as
    a curve - raw/loose anchored at the ungraded-equivalent grade (PSA 5 by
    Andrew's rule), then the graded rungs - and the answer is read off it by
    linear interpolation. So a half grade lands between its neighbours, and
    a grade whose own rung is missing interpolates from the points that DO
    exist rather than inheriting a higher rung's money.

    (The old code mapped 8.5 -> the Grade-9 field and fell back to
    graded-price whenever the target field was missing, so low grades
    silently inherited Grade-9 money - a CGC 8.5 Topsun Charizard was
    quoted $6,718 instead of ~$2,550.)
    """
    top_field = GRADER_TOP_FIELD.get((grader or "").lower())
    if top_field and printed_grade is not None and float(printed_grade) >= 10:
        direct = _field_cents(product, top_field)
        if direct is not None:
            return direct, f"{top_field} ({grader.upper()} 10 published price)"

    raw = _field_cents(product, RAW_FIELD)
    points = [(g, f, _field_cents(product, f)) for g, f in GUIDE_GRADE_LADDER]
    points = [(g, f, c) for g, f, c in points if c is not None]
    if eff_grade is None:
        return raw, "loose (ungraded)"
    if raw is not None:
        points = [(float(comps_mod.UNGRADED_GRADE), RAW_FIELD, raw)] + points
    if not points:
        return None, "no usable price fields"
    points.sort(key=lambda p: p[0])
    if eff_grade <= points[0][0]:
        if eff_grade == points[0][0]:
            return points[0][2], points[0][1]
        if points[0][1] == RAW_FIELD:
            return points[0][2], f"{RAW_FIELD} (at or below raw-equivalent)"
        # Below every price point we have, and the lowest is a GRADED rung:
        # quoting it would hand a low grade a high grade's money. No guide
        # value is the honest answer - comps carry the row instead.
        return None, (f"grade {eff_grade:g} below lowest available field "
                      f"({points[0][1]} @ {points[0][0]:g}) - no guide value")
    if eff_grade >= points[-1][0]:
        return points[-1][2], points[-1][1]
    lower = max((p for p in points if p[0] <= eff_grade), key=lambda p: p[0])
    if lower[0] == eff_grade:
        return lower[2], lower[1]
    upper = min((p for p in points if p[0] > eff_grade), key=lambda p: p[0])
    span = upper[0] - lower[0]
    w = (eff_grade - lower[0]) / span if span else 0.0
    return (lower[2] + (upper[2] - lower[2]) * w,
            f"{lower[1]}<->{upper[1]} interpolated at grade {eff_grade:g}")


@dataclass
class GuideQuote:
    """What PriceCharting knows about ONE specific asset.

    `match`/`score` are as important as `value`: the engine is allowed to let
    the guide lead only when we actually landed the card.  A value with a
    weak match is browse-only information, not a bid input.
    """
    value: float | None = None
    match: str = MATCH_NONE
    score: float = 0.0
    product_id: str | None = None
    product_name: str | None = None
    console_name: str | None = None
    genre: str | None = None
    sales_volume: int | None = None
    epid: str | None = None
    how: str = ""
    note: str = ""

    @property
    def landed(self) -> bool:
        """True when this product IS the card, not merely its family."""
        return self.match in (MATCH_EXACT, MATCH_STRONG) and self.value


def guide_db_path(config: dict) -> str:
    """Absolute history.db path, resolved exactly the way main.py resolves it.

    The guide used to connect to a bare relative ``"history.db"``.  Whenever a
    PriceGuide was built from a config that had not already been through
    main.py's path resolution, sqlite silently CREATED a second guide cache in
    whatever directory the process happened to start in - on 2026-07-26 that
    left a stray 28KB history.db (guide_cache / guide_meta /
    guide_product_cache) sitting in the project root, competing with the real
    20MB database/history.db.  Cached guide values written there were invisible
    to every later run, so the scanner paid for PriceCharting calls it had
    already made.

    Everything resolves against config.yaml's folder - the same rule
    code/paths.py enforces for every other file the scanner touches.
    """
    dbc = config.get("database") or {}
    db_file = dbc.get("file") or paths.DEFAULT_DB
    if os.path.isabs(db_file):
        return db_file
    return os.path.join(paths.base_dir(config), db_file)


class PriceGuide:
    def __init__(self, config: dict):
        keys = config.get("api_keys", {})
        self.pc_token = (keys.get("pricecharting") or {}).get("token")
        self.ptcg_key = (keys.get("pokemontcg") or {}).get("api_key")
        self.session = requests.Session()
        self._cache: dict[str, float | None] = {}
        self._pc_products: dict[str, dict] = {}
        self._pc_lock = threading.Lock()
        self._db_lock = threading.RLock()
        pc_cfg = config.get("pricecharting", {}) or {}
        try:
            configured_delay = float(
                pc_cfg.get("request_delay_seconds", 1.05))
        except (TypeError, ValueError):
            configured_delay = 1.05
        # PriceCharting's paid API documentation still limits every
        # subscription to one call per second.  Never allow configuration
        # to violate that contract; 1.05s gives clock/network jitter room.
        self._pc_delay = max(1.0, configured_delay)
        self._last_pc_request = 0.0
        # pokemontcg.io is frequently slow/down; after 3 consecutive
        # failures stop calling it for the rest of the run (each timeout
        # used to cost 30s of scan time, repeatedly)
        self._ptcg_fails = 0
        # Non-rate-limit failures still use a breaker.  A 429 is handled
        # separately: wait, retry once, then leave the next request paced.
        self._pc_fails = 0
        self._pc_wait_until = 0.0
        # Per-run ceiling on outward PriceCharting calls (searches + product
        # fetches). Cached lookups are free and never counted.
        algo = config.get("algorithm", {}) or {}
        # How far ahead the best candidate must be before we call it THE
        # card rather than a near-tie in the same set.
        self.match_margin = float(algo.get("guide_match_margin", 0.06))
        self.guide_hosts = [h.rstrip("/") for h in
                            (algo.get("guide_hosts") or DEFAULT_GUIDE_HOSTS)]
        from .guide_csv import GuideCsvIndex, load_index
        # Only ever read CSVs from an EXPLICIT project folder. Falling back
        # to "." would make the guide load whatever happened to be in the
        # working directory - the same mistake that once scattered stray
        # history.db files around, and here it also meant re-parsing a 19MB
        # guide on every construction.
        base = (config or {}).get("_config_dir")
        try:
            self.csv_index = load_index(base) if base else GuideCsvIndex("")
        except Exception:                              # noqa: BLE001
            log.warning("local price CSVs could not be loaded - "
                        "falling back to the API only")
            self.csv_index = GuideCsvIndex("")
        self.lookup_budget = int(algo.get("guide_lookups_per_run", 400))
        self._lookups_left = self.lookup_budget
        raw_buckets = algo.get("guide_lookups_per_run_by_category") or {}
        self._category_lookups_left = {
            str(category): int(limit)
            for category, limit in raw_buckets.items()}
        self._budget_skips = 0
        # True when the last guide lookup failed due to a request error
        # (timeout/429/etc.) rather than a genuine "no match" - such misses
        # must not be cached or they poison the guide cache for the TTL
        self._fetch_errored = False
        # persistent cache: guide prices move slowly, no need to re-query
        # the (rate-limited) guide APIs every run
        dbc = config.get("database", {})
        self.ttl_days = dbc.get("guide_cache_days", 7)
        self._db = None
        try:
            self._db = sqlite3.connect(
                guide_db_path(config), check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS guide_cache("
                             "query TEXT PRIMARY KEY, value REAL, ts TEXT)")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS guide_product_cache("
                "query TEXT PRIMARY KEY, payload TEXT NOT NULL, ts TEXT)")
            # one-time self-heal: if we now have a key, drop any cached
            # misses (Nones) - they were likely recorded before the key was
            # added and would otherwise stay poisoned for the whole TTL
            if self.pc_token or self.ptcg_key:
                self._db.execute("DELETE FROM guide_cache WHERE value IS NULL")
                self._db.commit()
            self._expire_stale_cache_version()
        except sqlite3.Error:
            self._db = None
        # distinct grade->field routings already logged this run (evidence
        # in scan.log that the cross-grader shift is being applied, without
        # a line per lookup)
        self._logged_routes: set[str] = set()

    def _expire_stale_cache_version(self) -> None:
        """Drop guide values computed by an older pricing rule.

        Guide prices are cached for `guide_cache_days` (7). Without this,
        the 2026-07-25 grade-shift fix would have been invisible for a week:
        the cache held 'Topsun Charizard 1997' at $6,718 for PSA 9, CGC 9,
        CGC 8.5 AND the typo 'CGC 85' - four different grades, one price.
        Bump GUIDE_CACHE_VERSION whenever the pricing rule changes.
        """
        if not self._db:
            return
        with self._db_lock:
            self._db.execute("CREATE TABLE IF NOT EXISTS guide_meta("
                             "key TEXT PRIMARY KEY, value TEXT)")
            row = self._db.execute(
                "SELECT value FROM guide_meta WHERE key='cache_version'"
            ).fetchone()
            if row and row[0] == GUIDE_CACHE_VERSION:
                return
            n = self._db.execute(
                "SELECT COUNT(*) FROM guide_cache").fetchone()[0]
            self._db.execute("DELETE FROM guide_cache")
            self._db.execute("INSERT OR REPLACE INTO guide_meta VALUES "
                             "('cache_version', ?)", (GUIDE_CACHE_VERSION,))
            self._db.commit()
        log.warning("price guide: cleared %d cached values from an older "
                    "valuation-rule version (new version: %s)", n,
                    GUIDE_CACHE_VERSION)

    def _log_grade_route(self, gi, eff, how: str) -> None:
        label = (f"{gi[0].upper()} {gi[1]} -> PSA {gi[2]}" if gi
                 else "ungraded -> raw")
        key = f"{label} | {how}"
        if key not in self._logged_routes:
            self._logged_routes.add(key)
            log.info("pricecharting grade routing: %s", key)

    def _db_get(self, query: str):
        """(hit, value) - hit=True even when the cached value is None."""
        if not self._db:
            return False, None
        with self._db_lock:
            row = self._db.execute(
                "SELECT value, ts FROM guide_cache WHERE query=?",
                (query,)).fetchone()
        if not row:
            return False, None
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.ttl_days)
        if datetime.fromisoformat(row[1]) < cutoff:
            return False, None
        return True, row[0]

    def _db_put(self, query: str, value) -> None:
        if self._db:
            with self._db_lock:
                self._db.execute(
                    "INSERT OR REPLACE INTO guide_cache VALUES (?,?,?)",
                    (query, value,
                     datetime.now(timezone.utc).isoformat()))
                self._db.commit()

    def _product_db_get(self, query: str) -> tuple[bool, dict | None]:
        if not self._db:
            return False, None
        with self._db_lock:
            row = self._db.execute(
                "SELECT payload, ts FROM guide_product_cache WHERE query=?",
                (query,)).fetchone()
        if not row:
            return False, None
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.ttl_days)
        try:
            if datetime.fromisoformat(row[1]) < cutoff:
                return False, None
            payload = json.loads(row[0])
        except (TypeError, ValueError, json.JSONDecodeError):
            return False, None
        return isinstance(payload, dict), (
            payload if isinstance(payload, dict) else None)

    def _product_db_put(self, query: str, payload: dict) -> None:
        if not self._db:
            return
        with self._db_lock:
            self._db.execute(
                "INSERT OR REPLACE INTO guide_product_cache VALUES (?,?,?)",
                (query, json.dumps(payload, separators=(",", ":")),
                 datetime.now(timezone.utc).isoformat()))
            self._db.commit()

    def guide_value(self, query: str) -> float | None:
        cache_key = _guide_cache_key(query)
        if cache_key in self._cache:
            return self._cache[cache_key]
        hit, val = self._db_get(cache_key)
        # Backward compatibility with values cached before canonical keys
        # were introduced. Promote a hit so future variants share it.
        if not hit and cache_key != query:
            hit, val = self._db_get(query)
            if hit:
                self._db_put(cache_key, val)
        if not hit:
            self._fetch_errored = False
            val = self._pricecharting(query)
            if val is None:
                val = self._pokemontcg(query)
            # Never persist a miss when we have no credentials to try with -
            # otherwise a run made before the token was added poisons the
            # cache with Nones for the whole TTL. Only cache real results,
            # or genuine misses made WITH a working key. Misses caused by
            # request errors (429 storm, timeouts) are also never cached.
            have_keys = bool(self.pc_token or self.ptcg_key)
            if val is not None or (have_keys and not self._fetch_errored):
                self._db_put(cache_key, val)
        self._cache[cache_key] = val
        return val

    # ---- PriceCharting request lane ----
    def _pc_call(self, path: str, params: dict,
                 host: str | None = None) -> dict | None:
        """One paced, breakered PriceCharting request.

        MUST be called while holding `self._pc_lock`. That lock is both the
        one-call-per-second rate gate the paid API documents and the atomic
        single-flight gate, so concurrent valuations of the same product
        cannot stampede the endpoint.
        """
        if self._pc_fails >= 3:
            self._fetch_errored = True
            note_api("pricecharting", "skipped")
            return None
        # Retry one 429 after its advertised cooldown. Other failures return
        # immediately and feed the breaker.
        for attempt in range(2):
            now = time.monotonic()
            wait = max(0.0,
                       self._pc_delay - (now - self._last_pc_request),
                       self._pc_wait_until - now)
            if wait > 0:
                time.sleep(wait)
            self._last_pc_request = time.monotonic()
            try:
                r = self.session.get(
                    f"{host or self.guide_hosts[0]}/api/{path}",
                    params=dict(params, t=self.pc_token), timeout=30)
                r.raise_for_status()
                payload = r.json()
                if not isinstance(payload, dict):
                    raise ValueError(
                        "PriceCharting response was not an object")
                self._pc_fails = 0
                self._pc_wait_until = 0.0
                self._fetch_errored = False
                note_api("pricecharting", "ok")
                return payload
            except requests.RequestException as e:
                self._fetch_errored = True
                note_api("pricecharting", "failed")
                resp = getattr(e, "response", None)
                if resp is not None and resp.status_code == 429:
                    secs = _retry_after_seconds(resp)
                    self._pc_wait_until = time.monotonic() + secs
                    if attempt == 0:
                        log.warning("pricecharting: rate limited (429) - "
                                    "waiting %.0fs and retrying once", secs)
                        continue
                    log.warning("pricecharting: still rate limited after one "
                                "paced retry; next lookup waits %.0fs", secs)
                    return None
                self._pc_fails += 1
                if self._pc_fails < 3:
                    log.warning("pricecharting failed: %s", redact_text(e))
                if self._pc_fails == 3:
                    log.warning("pricecharting: 3 consecutive failures - "
                                "backing off for the rest of this run")
                return None
            except (TypeError, ValueError) as e:
                self._fetch_errored = True
                note_api("pricecharting", "failed")
                self._pc_fails += 1
                log.warning("pricecharting response failed: %s",
                            redact_text(e))
                return None
        return None

    def _cached_product(self, cache_key: str, path: str,
                        params: dict, host: str | None = None,
                        budget_category: str | None = None) -> dict | None:
        """Fetch-once-and-remember for any PriceCharting payload."""
        if cache_key in self._pc_products:
            return self._pc_products[cache_key]
        hit, product = self._product_db_get(cache_key)
        if hit and product is not None:
            self._pc_products[cache_key] = product
            return product
        with self._pc_lock:
            if cache_key in self._pc_products:
                return self._pc_products[cache_key]
            hit, product = self._product_db_get(cache_key)
            if hit and product is not None:
                self._pc_products[cache_key] = product
                return product
            # Budget guard. PriceCharting's documented default application
            # quota is 5,000 calls/day and the paid API is paced at one call
            # per second. Identity resolution costs a search plus a product
            # fetch, so an ungoverned run can burn the day's quota (and
            # hours of wall clock) in a single sweep: on 2026-07-26 one BIN
            # sweep made 661 calls in 19 minutes and was still going.
            category_left = self._category_lookups_left.get(budget_category)
            if self._lookups_left <= 0 or (
                    category_left is not None and category_left <= 0):
                self._budget_skips += 1
                if self._budget_skips == 1:
                    log.warning(
                        "pricecharting: run/category budget is spent "
                        "(total=%d, category=%s) - "
                        "remaining rows use comps only. Raise "
                        "algorithm.guide_lookups_per_run if this is too low.",
                        self.lookup_budget, budget_category or "unreserved")
                note_api("pricecharting", "skipped")
                return None
            self._lookups_left -= 1
            if category_left is not None:
                self._category_lookups_left[budget_category] = category_left - 1
            product = self._pc_call(path, params, host=host)
            if product is None:
                return None
            self._pc_products[cache_key] = product
            self._product_db_put(cache_key, product)
            return product

    # ---- identity-based resolution (the card, not the phrase) ----
    def quote(self, ident: CardIdentity,
              category: str | None = None) -> GuideQuote:
        """Resolve against each configured guide host, first landing wins.

        pricecharting.com carries TCG, video games, comics, Funko and LEGO
        but NOT sports cards - searching it for a 1952 Mantle returns Funko
        POPs and LEGO sets. sportscardspro.com is the same company's sports
        catalogue with an identical API, and the same token reached it.

        Rather than classify a listing as "sports" or "not sports" up front -
        a taxonomy that would misfile Topps Chrome Disney the moment it saw
        "Topps" - we simply ask each host and keep the first confident
        answer. Only rows that fail on the primary cost a second lookup, and
        every result is cached permanently per host.
        """
        # Decide which card guide - if any - may speak for this category
        # BEFORE consulting anything, including the local CSVs.
        #
        # 2026-08-02: this block used to sit below the CSV lookup, so
        # emptying `hosts` for Sports Memorabilia stopped the API calls and
        # nothing else. A signed photo went on matching the card:
        #
        #   "1986 Fleer Michael Jordan #57 Signed Photo PSA 9"
        #     -> $42,639.45  match=exact  from graded-price
        #
        # which is the PSA 9 price of the rookie CARD, quoted at full
        # confidence for a photograph. Downloading the four sports
        # catalogues made it worse, because far more memorabilia titles now
        # find a matching card row locally.
        hosts = list(self.guide_hosts)
        if category == "Sports Cards":
            hosts = [host for host in hosts if "sportscardspro" in host]
        elif category in {"Video Games", "Pokemon Cards", "Other"}:
            primary = [host for host in hosts if "sportscardspro" not in host]
            hosts = primary or hosts
        if category in NO_CARD_GUIDE:
            hosts = []

        # Local CSVs first: same data, same columns, zero latency and zero
        # quota. Every set you download is a set that stops costing calls.
        if category not in NO_CARD_GUIDE and len(self.csv_index):
            local = self._quote_from_rows(
                ident, self.csv_index.search(ident.guide_query()),
                source="local CSV", category=category)
            if local.landed:
                return local
        if category in NO_CARD_GUIDE:
            return GuideQuote(note=f"no card price guide applies to "
                                   f"{category} - comps only")
        best_miss = None
        for host in hosts:
            q = self._quote_from(ident, host, category=category)
            if q.landed:
                return q
            if best_miss is None or q.score > best_miss.score:
                best_miss = q
        return best_miss or GuideQuote(note="no guide host configured")

    def _quote_from(self, ident: CardIdentity, host: str,
                    category: str | None = None) -> GuideQuote:
        """Search one guide host, then price the winning product."""
        q = ident.guide_query()
        if not self.pc_token or not q:
            return GuideQuote(note="no PriceCharting token or query")
        tag = "" if host == self.guide_hosts[0] else f"@{_host_tag(host)}"
        listing = self._cached_product(
            f"search:{q}{tag}", "products", {"q": q}, host=host,
            budget_category=category)
        if not listing or listing.get("status") == "error":
            return GuideQuote(note=f"no PriceCharting match for {q!r}")

        def fetch(best):
            pid = str(best.get("id") or "")
            if not pid:
                return best
            return self._cached_product(
                f"id:{pid}{tag}", "product", {"id": pid}, host=host,
                budget_category=category)

        return self._quote_from_rows(
            ident, candidates_of(listing), source=_host_tag(host), fetch=fetch,
            category=category)

    def _quote_from_rows(self, ident: CardIdentity, rows, source: str,
                         fetch=None, category: str | None = None) -> GuideQuote:
        """Score candidate product rows and price the winner.

        Shared by the paid API and the local CSVs on purpose: a row from a
        downloaded price guide has exactly the same column names as an API
        response, so both are held to one standard of proof.

        The old path sent a watchlist phrase to /api/product, which returns
        PriceCharting's single best guess with no indication of how good that
        guess was.  A set-level phrase therefore produced a set-level product
        and a confident number: eight Disney parallels shared one $1,069.60,
        and an $18 plastic figure inherited $2,821 from "Superman 1940".

        /api/products returns up to 20 candidates.  We score every one of
        them against the structured identity and keep the best - or, when
        nothing scores well enough, we return no value and say why.
        """
        q = ident.guide_query()
        # Belt and braces with the gate in quote(). This is the function
        # that actually turns a row into money, so it refuses categories no
        # card guide may value even if a future caller forgets to check.
        if category in NO_CARD_GUIDE:
            return GuideQuote(match=MATCH_NONE,
                              note=f"no card price guide applies to "
                                   f"{category} - comps only")
        candidates = [r for r in (rows or []) if isinstance(r, dict)]
        if category == "Sports Cards":
            guarded = []
            for candidate in candidates:
                origin = str(candidate.get("_guide-host") or "")
                if origin and origin != "sportscardspro":
                    continue
                genre = genre_class(candidate.get("genre"))
                if genre and genre != "card":
                    continue
                other = CardIdentity.from_text(
                    f"{candidate.get('product-name', '')} "
                    f"{candidate.get('console-name', '')}".strip())
                if ident.number and other.number != ident.number:
                    continue
                if ident.year and other.year != ident.year:
                    continue
                if (ident.set_tokens and not
                        (set(ident.set_tokens) & set(other.set_tokens))):
                    continue
                guarded.append(candidate)
            candidates = guarded
        scored = sorted(
            ((ident.score_candidate(c.get("product-name", ""),
                                    c.get("console-name", "")), c)
             for c in candidates), key=lambda pair: -pair[0])
        best, best_score = (scored[0][1], scored[0][0]) if scored else (None, 0.0)

        band = match_band(best_score)
        if best is None or band == MATCH_NONE:
            return GuideQuote(
                match=MATCH_NONE, score=best_score,
                note=(f"{len(candidates)} {source} candidate(s) for "
                      f"{q!r}, best match only {best_score:.0%}"))

        # Margin gate. A high score means "this product fits the words"; it
        # does NOT mean "no other product fits them better". When the runner
        # up is nearly as good we have not identified the card, we have
        # merely found its neighbourhood - and a confident wrong answer is
        # the most expensive failure this system can produce.
        #
        # Real examples from 2026-07-26: `Cinderella [Refractor]` 57% vs the
        # correct `Cinderella [Pink]` 54%; and a Base Set Charizard scoring
        # 82% on `Pokemon Chinese CSM2cC` with `Crystal Guardians` at 81%.
        # Both are ties dressed up as convictions.
        # Runners-up we have already ruled out on stated evidence are not
        # rivals. If the listing says "1st Edition" and the winner is the 1st
        # Edition product, the unlimited printing sitting 5% behind is a
        # candidate we correctly rejected - not proof that we are guessing.
        top_ident = CardIdentity.from_text(
            f"{best.get('product-name', '')} "
            f"{best.get('console-name', '')}".strip())
        rival = None
        for score_i, cand in scored[1:]:
            other = CardIdentity.from_text(
                f"{cand.get('product-name', '')} "
                f"{cand.get('console-name', '')}".strip())
            if ident.discriminates(top_ident, other):
                continue
            rival = (score_i, cand)
            break

        if rival is not None:
            margin = best_score - rival[0]
            if margin < self.match_margin:
                return GuideQuote(
                    match=MATCH_NONE, score=best_score,
                    product_name=best.get("product-name"),
                    console_name=best.get("console-name"),
                    # Name the SET on both sides. Two candidates called
                    # "Barry Bonds #11T" read as nonsense - they are the
                    # same card twice - until you see that one is Topps
                    # Traded and the other Topps Traded Tiffany, which is
                    # the whole reason it cannot choose.
                    note=(f"ambiguous: "
                          f"{best.get('product-name')!r} in "
                          f"{best.get('console-name') or '?'!r} "
                          f"({best_score:.0%}) vs "
                          f"{rival[1].get('product-name')!r} in "
                          f"{rival[1].get('console-name') or '?'!r} "
                          f"({rival[0]:.0%}) - only {margin:.0%} apart. "
                          "Name the set to choose between them."))

        pid = str(best.get("id") or "")
        product = fetch(best) if fetch else best
        if not product or product.get("status") == "error":
            return GuideQuote(match=MATCH_NONE, score=best_score,
                              note=f"PriceCharting product {pid} unavailable")

        cents, how = _guide_cents(product, ident.grade,
                                  grader=ident.grader,
                                  printed_grade=ident.printed_grade)
        name = product.get("product-name") or best.get("product-name")
        console = product.get("console-name") or best.get("console-name")
        if not cents:
            # We identified the card but PriceCharting publishes no price at
            # this grade. That is a different outcome from "wrong product",
            # and saying so plainly saves the next person re-investigating a
            # match that was actually correct.
            return GuideQuote(
                match=MATCH_NONE, score=best_score, product_id=pid or None,
                product_name=name, console_name=console,
                genre=product.get("genre"),
                sales_volume=_int_or_none(product.get("sales-volume")),
                how=how,
                note=(f"matched {name!r} at {best_score:.0%} but "
                      f"PriceCharting has no price at this grade ({how})"))
        quote = GuideQuote(
            value=(cents / 100.0) if cents else None,
            match=band, score=best_score, product_id=pid or None,
            product_name=name, console_name=console,
            genre=product.get("genre"),
            sales_volume=_int_or_none(product.get("sales-volume")),
            epid=product.get("epid"), how=how,
            note=f"{source}: {name!r} ({console}) matched {best_score:.0%}")

        # PriceCharting's own genre is free corroboration that we landed the
        # right KIND of object. A "card" identity matched to a Comic product
        # is a resolution failure, not a price.
        want = ident.object_class
        got = genre_class(product.get("genre"))
        if got and want != "unknown" and got != want:
            return GuideQuote(
                match=MATCH_NONE, score=best_score, product_id=pid or None,
                product_name=name, console_name=console,
                genre=product.get("genre"),
                note=(f"PriceCharting genre {product.get('genre')!r} is a "
                      f"{got}, listing is a {want} - not the same object"))
        self._log_grade_route(
            (ident.grader, ident.printed_grade, ident.grade)
            if ident.grader else None, ident.grade, how)
        return quote

    # ---- legacy phrase lookup (kept for query-level guide values) ----
    def _pricecharting(self, query: str) -> float | None:
        if not self.pc_token:
            return None
        product_key = _pricecharting_product_key(query)
        if not product_key:
            return None
        product = self._cached_product(
            product_key, "product", {"q": product_key})
        if not product or product.get("status") == "error":
            return None
        name = (f"{product.get('product-name', '')} "
                f"{product.get('console-name', '')}")
        # score against the grade-stripped query so the grade token
        # doesn't drag the match below threshold
        if title_match_score(product_key, name) < 0.4:
            return None
        # pennies -> dollars, priced at the PSA-EQUIVALENT grade so a
        # CGC/BGS/SGC slab is never quoted a full grade too high.
        gi = grade_info(query)
        eff = float(gi[2]) if gi else None
        cents, how = _guide_cents(product, eff)
        self._log_grade_route(gi, eff, how)
        return cents / 100.0 if cents else None

    # ---- pokemontcg.io (raw cards) ----
    def _pokemontcg(self, query: str) -> float | None:
        if GRADE_RE.search(query):
            return None  # TCGPlayer prices are for raw cards only
        if not self.ptcg_key:
            # source_health calls this source "disabled" when no api_key is
            # configured (see source_health._configured_sources).  Until
            # 2026-07-26 the guide ignored that and made ANONYMOUS calls
            # anyway: the 11:54 full run showed pokemontcg/guide "disabled"
            # while logging 7 ok / 10 failed / 348 skipped.  A source cannot
            # be both disabled and live.  No key means no call.
            note_api("pokemontcg.io", "skipped")
            return None
        if self._ptcg_fails >= 3:
            note_api("pokemontcg.io", "skipped")
            return None  # circuit tripped for this run
        headers = {"X-Api-Key": self.ptcg_key} if self.ptcg_key else {}
        name = re.sub(r"\b(holo|1st edition|shadowless|#?\d+/\d+)\b", "",
                      query, flags=re.I).strip()
        try:
            r = self.session.get("https://api.pokemontcg.io/v2/cards",
                                 params={"q": f'name:"{name.split()[0]}"',
                                         "pageSize": 20},
                                 headers=headers, timeout=8)
            r.raise_for_status()
            self._ptcg_fails = 0
            note_api("pokemontcg.io", "ok")
            best, best_score = None, 0.4
            for card in r.json().get("data", []):
                label = f"{card.get('name', '')} {card.get('set', {}).get('name', '')} {card.get('number', '')}"
                s = title_match_score(query, label)
                prices = ((card.get("tcgplayer") or {}).get("prices") or {})
                market = None
                for variant in prices.values():
                    market = variant.get("market") or market
                if s > best_score and market:
                    best, best_score = market, s
            return best
        except requests.RequestException as e:
            note_api("pokemontcg.io", "failed")
            self._ptcg_fails += 1
            log.warning("pokemontcg.io failed: %s", redact_text(e))
            if self._ptcg_fails == 3:
                log.warning("pokemontcg.io: 3 consecutive failures - "
                            "skipping it for the rest of this run")
            return None
