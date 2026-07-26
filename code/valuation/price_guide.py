"""Price-guide reference values.

Sources (both optional, keyed via config):
  - PriceCharting API: great for graded Pokemon cards (has PSA/BGS/CGC prices)
  - pokemontcg.io: TCGPlayer market price for raw (ungraded) cards
Returns None when no source is available/matches - the blender handles it.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests

from scrapers.base import note_api
from security import redact_text

from . import comps as comps_mod
from .comps import title_match_score, grade_info, GRADE_RE

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
# Bump this whenever the grade->price rule changes, so cached values priced
# under the old rule are dropped instead of being served for the whole TTL.
GUIDE_CACHE_VERSION = "2026-07-25-landed-trust-v2"


def _field_cents(product: dict, field: str) -> float | None:
    val = product.get(field)
    if not val:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _guide_cents(product: dict, eff_grade: float | None) -> tuple[float | None, str]:
    """(cents, explanation) for a PSA-equivalent grade.

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


class PriceGuide:
    def __init__(self, config: dict):
        keys = config.get("api_keys", {})
        self.pc_token = (keys.get("pricecharting") or {}).get("token")
        self.ptcg_key = (keys.get("pokemontcg") or {}).get("api_key")
        self.session = requests.Session()
        self._cache: dict[str, float | None] = {}
        # pokemontcg.io is frequently slow/down; after 3 consecutive
        # failures stop calling it for the rest of the run (each timeout
        # used to cost 30s of scan time, repeatedly)
        self._ptcg_fails = 0
        # same idea for PriceCharting: a 429 storm (rate limit) means every
        # further call this run will also fail - after 3 consecutive
        # failures stop hammering their server for the rest of the run
        self._pc_fails = 0
        # politest 429 handling: when the response carries Retry-After,
        # honor it exactly - zero calls until that moment, resume right
        # after. (Monotonic clock so system clock changes can't confuse it.)
        self._pc_wait_until = 0.0
        # True when the last guide lookup failed due to a request error
        # (timeout/429/etc.) rather than a genuine "no match" - such misses
        # must not be cached or they poison the guide cache for the TTL
        self._fetch_errored = False
        # persistent cache: guide prices move slowly, no need to re-query
        # the (rate-limited) guide APIs every run
        dbc = config.get("database", {})
        self.ttl_days = dbc.get("guide_cache_days", 7)
        try:
            self._db = sqlite3.connect(dbc.get("file", "history.db"))
            self._db.execute("CREATE TABLE IF NOT EXISTS guide_cache("
                             "query TEXT PRIMARY KEY, value REAL, ts TEXT)")
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
        self._db.execute("CREATE TABLE IF NOT EXISTS guide_meta("
                         "key TEXT PRIMARY KEY, value TEXT)")
        row = self._db.execute(
            "SELECT value FROM guide_meta WHERE key='cache_version'"
        ).fetchone()
        if row and row[0] == GUIDE_CACHE_VERSION:
            return
        n = self._db.execute("SELECT COUNT(*) FROM guide_cache").fetchone()[0]
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
        row = self._db.execute("SELECT value, ts FROM guide_cache WHERE query=?",
                               (query,)).fetchone()
        if not row:
            return False, None
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.ttl_days)
        if datetime.fromisoformat(row[1]) < cutoff:
            return False, None
        return True, row[0]

    def _db_put(self, query: str, value) -> None:
        if self._db:
            self._db.execute("INSERT OR REPLACE INTO guide_cache VALUES (?,?,?)",
                             (query, value, datetime.now(timezone.utc).isoformat()))
            self._db.commit()

    def guide_value(self, query: str) -> float | None:
        if query in self._cache:
            return self._cache[query]
        hit, val = self._db_get(query)
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
                self._db_put(query, val)
        self._cache[query] = val
        return val

    # ---- PriceCharting ----
    def _pricecharting(self, query: str) -> float | None:
        if not self.pc_token:
            return None
        if self._pc_fails >= 3:
            self._fetch_errored = True   # skipped, not a real miss
            note_api("pricecharting", "skipped")
            return None
        if time.monotonic() < self._pc_wait_until:
            self._fetch_errored = True   # rate-limited pause, not a miss
            note_api("pricecharting", "skipped")
            return None
        try:
            r = self.session.get("https://www.pricecharting.com/api/product",
                                 params={"t": self.pc_token,
                                         "q": re.sub(GRADE_RE, "", query)},
                                 timeout=30)
            r.raise_for_status()
            self._pc_fails = 0
            note_api("pricecharting", "ok")
            p = r.json()
            if p.get("status") == "error":
                return None
            name = f"{p.get('product-name', '')} {p.get('console-name', '')}"
            # score against the grade-stripped query so the grade token
            # doesn't drag the match below threshold
            if title_match_score(re.sub(GRADE_RE, "", query), name) < 0.4:
                return None
            # pennies -> dollars, priced at the PSA-EQUIVALENT grade so a
            # CGC/BGS/SGC slab is never quoted a full grade too high.
            gi = grade_info(query)
            eff = float(gi[2]) if gi else None
            cents, how = _guide_cents(p, eff)
            self._log_grade_route(gi, eff, how)
            return cents / 100.0 if cents else None
        except requests.RequestException as e:
            self._fetch_errored = True
            note_api("pricecharting", "failed")
            self._pc_fails += 1
            # 429 with Retry-After: the server told us exactly when we're
            # welcome back - honor it instead of burning failures
            resp = getattr(e, "response", None)
            if resp is not None and resp.status_code == 429:
                ra = (resp.headers or {}).get("Retry-After", "")
                secs = int(ra) if str(ra).isdigit() else 60
                secs = min(max(secs, 30), 3600)   # sane bounds: 30s-1h
                self._pc_wait_until = time.monotonic() + secs
                log.warning("pricecharting: rate limited (429) - pausing "
                            "guide lookups for %ds", secs)
            elif self._pc_fails < 3:
                log.warning("pricecharting failed: %s", redact_text(e))
            if self._pc_fails == 3:
                log.warning("pricecharting: 3 consecutive failures - "
                            "backing off for the rest of this run")
            return None

    # ---- pokemontcg.io (raw cards) ----
    def _pokemontcg(self, query: str) -> float | None:
        if GRADE_RE.search(query):
            return None  # TCGPlayer prices are for raw cards only
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
