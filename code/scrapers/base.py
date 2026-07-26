"""Base scraper with polite HTTP helpers."""
from __future__ import annotations

import collections
import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from security import redact_text, redact_url

import paths

# curl_cffi impersonates a real Chrome browser at the TLS/HTTP2 level.
# Sites like eBay (418 "I'm a teapot") and 130point (Cloudflare 403) block
# plain python clients by TLS fingerprint alone - headers can't fix that.
# Optional dependency: if missing, we fall back to plain requests.
try:
    from curl_cffi import requests as curl_requests
except ImportError:                                    # pragma: no cover
    curl_requests = None

log = logging.getLogger(__name__)

# exception types _get must treat as "request failed" (never a crash)
_HTTP_ERRORS: tuple = (requests.RequestException,)
if curl_requests is not None:
    for _mod, _name in (("curl_cffi.requests.exceptions", "RequestException"),
                        ("curl_cffi.requests.errors", "RequestsError")):
        try:
            import importlib
            _HTTP_ERRORS += (getattr(importlib.import_module(_mod), _name),)
        except (ImportError, AttributeError):
            pass

# ---------------------------------------------------------------------------
# Per-run API usage tally. Andrew's rule is "after 3 straight failures, leave
# that endpoint alone for the rest of the run" - this is the evidence that it
# actually happened. Every outward call records ok / failed / skipped, and
# main.py prints the summary as the run finishes (in EVERY mode: the full
# scan and the BIN sweep are the same program, so they share this).
API_STATS: "collections.Counter" = collections.Counter()
_stats_lock = threading.Lock()


def note_api(endpoint: str, outcome: str) -> None:
    """Record one call attempt. outcome: 'ok' | 'failed' | 'skipped'."""
    with _stats_lock:
        API_STATS[(endpoint, outcome)] += 1


def reset_api_stats() -> None:
    with _stats_lock:
        API_STATS.clear()


def api_summary() -> str:
    """One-line-per-endpoint summary, quietest endpoints last."""
    with _stats_lock:
        stats = dict(API_STATS)
    if not stats:
        return "no outward API calls this run"
    endpoints: dict = {}
    for (endpoint, outcome), n in stats.items():
        endpoints.setdefault(endpoint, {})[outcome] = n
    parts = []
    for endpoint in sorted(endpoints,
                           key=lambda e: -sum(endpoints[e].values())):
        counts = endpoints[endpoint]
        bits = [f"{counts[k]} {k}" for k in ("ok", "failed", "skipped")
                if counts.get(k)]
        note = (" <- breaker open, left alone" if counts.get("skipped")
                else "")
        parts.append(f"{endpoint}: {', '.join(bits)}{note}")
    return " | ".join(parts)


def api_snapshot() -> dict[str, dict[str, int]]:
    """Structured copy of the per-run counters for health persistence."""
    with _stats_lock:
        stats = dict(API_STATS)
    endpoints: dict[str, dict[str, int]] = {}
    for (endpoint, outcome), n in stats.items():
        endpoints.setdefault(endpoint, {})[outcome] = int(n)
    return endpoints


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


class BaseScraper:
    site = "base"
    # fetched once per session before the first real HTML request: real
    # browsers arrive with cookies from browsing the site - clients that
    # hit search APIs cold, without ever loading a page, look like bots
    warmup_url: str | None = None

    def __init__(self, config: dict):
        self.config = config
        scraping_cfg = config.get("scraping", {})
        if curl_requests is not None:
            # real-browser TLS fingerprint; curl_cffi sets matching
            # User-Agent + header order itself (do NOT override UA - a
            # mismatched UA breaks the fingerprint story). Profile is
            # configurable (scraping.impersonate) so we can switch to
            # safari etc. without code changes if chrome gets blocked.
            profile = scraping_cfg.get("impersonate", "chrome")
            self.session = curl_requests.Session(impersonate=profile)
            self.impersonating = True
        else:
            self.session = requests.Session()
            self.session.headers["User-Agent"] = random.choice(USER_AGENTS)
            # retry transient failures: DNS hiccups, resets, 5xx. NEVER
            # auto-retry 429 - "too many requests" answered with two more
            # requests is built-in spam; the breaker handles rate limits
            retry = Retry(total=2, connect=2, backoff_factor=0.5,
                          status_forcelist=[500, 502, 503])
            self.session.mount("https://", HTTPAdapter(max_retries=retry))
            self.impersonating = False
            log.warning("%s: curl_cffi not installed - using plain HTTP "
                        "client, which eBay/130point may bot-block "
                        "(pip install curl_cffi)", self.site)
        scraping = config.get("scraping", {})
        # per-site delay override (e.g. 130point's lightweight API tolerates
        # a shorter politeness delay than eBay's HTML pages)
        self.delay = (scraping.get("site_delays") or {}).get(
            self.site, scraping.get("request_delay_seconds", 3.5))
        # fail fast on the html lane: a challenge-walled/blackholed page
        # used to hold the (serialized) lane hostage for a full 30s before
        # the cooldown even started. API calls keep the longer timeout -
        # they're authenticated and slow responses there are real work.
        self.html_timeout = scraping.get("html_timeout_seconds", 12)
        # queries now run in parallel threads sharing this scraper: this
        # lock keeps the HTML lane strictly one-request-at-a-time per site,
        # so politeness (delay between hits to the same host) is preserved
        self._html_lock = threading.Lock()
        # circuit breaker PER CHANNEL: html scraping and authenticated API
        # calls fail independently (eBay can block scraping while the API
        # is fine), so each lane trips on its own
        self.trip_after = scraping.get("circuit_breaker_failures", 3)
        self._streaks = {"api": 0, "html": 0}
        self._announced: set[str] = set()
        # bot-challenge tally for the WHOLE run: individual challenges often
        # clear after a cooldown retry (which resets the failure streak), but
        # a site that challenges us over and over is telling us to go away -
        # after this many challenges in one run, back off hard and persist
        # a cross-run cooldown (see note_challenge)
        self.challenge_backoff_after = scraping.get(
            "challenge_backoff_after", 3)
        self._challenge_count = 0
        # ---- persistent cross-run breaker ----
        # A per-run breaker resets every run, but cron launches a run every
        # 30 min - so a blocked site still got a fresh volley of failures
        # 48x/day. Tripped channels now write ".breaker_state.json" and stay
        # down across runs with exponential backoff: 30m, 1h, 2h ... cap 24h
        # (scraping.breaker_cooldown_minutes / breaker_cooldown_max_hours).
        # After the cooldown expires the next run probes normally; strikes
        # only reset after 24h without a new trip, so flapping (one lucky
        # probe between challenge storms) still escalates.
        self._cooldown_base_min = scraping.get("breaker_cooldown_minutes", 30)
        self._cooldown_cap_h = scraping.get("breaker_cooldown_max_hours", 24)
        state_dir = paths.folder(paths.base_dir(config), paths.STATE)
        self._state_file = os.path.join(state_dir, ".breaker_state.json")
        self._cooldown_until: dict[str, datetime] = {}
        try:
            with open(self._state_file) as f:
                state = json.load(f)
            for lane in ("api", "html"):
                ent = state.get(f"{self.site}/{lane}")
                if ent:
                    until = datetime.fromisoformat(ent["until"])
                    if datetime.now(timezone.utc) < until:
                        self._cooldown_until[lane] = until
        except (OSError, ValueError, KeyError):
            pass
        self._warmed = False
        # persistent cookie jar: a returning visitor with stable cookies
        # looks human; a fresh cookie-less client every 30-min sweep looks
        # like a bot. Saved after successful html requests, wiped when a
        # site challenges us anyway (stale/flagged jar - start fresh).
        self._cookie_file = os.path.join(state_dir,
                                         f".cookies_{self.site}.json")
        self._load_cookies()

    # ---- cookie persistence (best-effort; failures never break a scan) ----
    def _iter_jar(self):
        cookies = self.session.cookies
        return getattr(cookies, "jar", cookies)   # curl_cffi wraps a CookieJar

    def _load_cookies(self) -> None:
        try:
            if not os.path.exists(self._cookie_file):
                return
            loaded = 0
            with open(self._cookie_file) as f:
                jar = json.load(f)
            for c in jar:
                self.session.cookies.set(c["name"], c["value"],
                                         domain=c.get("domain", ""),
                                         path=c.get("path", "/"))
                loaded += 1
            if loaded:
                # returning visitor with cookies: skip the homepage warm-up
                # (challenge -> reset_cookies -> next run warms up fresh)
                self._warmed = True
        except Exception:
            pass

    def _save_cookies(self) -> None:
        try:
            data = [{"name": c.name, "value": c.value,
                     "domain": c.domain, "path": c.path}
                    for c in self._iter_jar()]
            if data:
                with open(self._cookie_file, "w") as f:
                    json.dump(data, f)
        except Exception:
            pass

    def reset_cookies(self) -> None:
        """Wipe the jar - called when a site challenges us despite cookies."""
        try:
            if os.path.exists(self._cookie_file):
                os.remove(self._cookie_file)
            self.session.cookies.clear()
        except Exception:
            pass

    _state_lock = threading.Lock()   # class-level: one state file, many scrapers

    def lane_tripped(self, lane: str) -> bool:
        until = self._cooldown_until.get(lane)
        if until is not None:
            if datetime.now(timezone.utc) < until:
                return True
            # cooldown expired - this run is the probe; if the site is
            # still hostile the breaker re-trips at the next strike level
            del self._cooldown_until[lane]
        return self._streaks[lane] >= self.trip_after

    @property
    def tripped(self) -> bool:      # html lane: used by comps warming etc.
        return self.lane_tripped("html")

    def _persist_trip(self, lane: str, reason: str) -> None:
        """Trip a lane for the rest of this run AND schedule a cross-run
        cooldown with exponential backoff (strike 1: 30m, 2: 1h, ... cap 24h).
        Strikes reset only after 24h without a new trip."""
        self._streaks[lane] = max(self._streaks[lane], self.trip_after)
        key = f"{self.site}/{lane}"
        now = datetime.now(timezone.utc)
        with self._state_lock:
            try:
                with open(self._state_file) as f:
                    state = json.load(f)
            except (OSError, ValueError):
                state = {}
            ent = state.get(key) or {}
            strikes = 1
            try:
                last = datetime.fromisoformat(ent["last_trip"])
                if now - last < timedelta(hours=24):
                    strikes = int(ent.get("strikes", 0)) + 1
            except (KeyError, ValueError):
                pass
            minutes = min(self._cooldown_base_min * 2 ** (strikes - 1),
                          self._cooldown_cap_h * 60)
            until = now + timedelta(minutes=minutes)
            state[key] = {"strikes": strikes, "last_trip": now.isoformat(),
                          "until": until.isoformat()}
            try:
                with open(self._state_file, "w") as f:
                    json.dump(state, f)
            except OSError:
                pass
        self._cooldown_until[lane] = until
        log.warning("%s/%s: %s - backing off %s (strike %d); next attempt "
                    "after %s", self.site, lane, reason,
                    ("%dh" % (minutes // 60) if minutes >= 60
                     else "%dm" % minutes), strikes,
                    until.astimezone().strftime("%H:%M"))

    def note_challenge(self, lane: str = "html") -> bool:
        """Record one bot-challenge page. Challenges that clear on a
        cooldown retry reset the FAILURE streak but still count here -
        being challenged 10x in one run means the site wants us gone.
        Returns True once the hard backoff has engaged (caller should stop)."""
        self._challenge_count += 1
        if self._challenge_count >= self.challenge_backoff_after:
            if self._challenge_count == self.challenge_backoff_after:
                self._persist_trip(
                    lane, "%d bot-challenges this run" % self._challenge_count)
                self.reset_cookies()   # flagged jar - start fresh after cooldown
            return True
        return False

    def _get(self, url: str, api: bool = False, **kwargs) -> requests.Response | None:
        """GET with delay, graceful failure, and per-channel circuit breaker.

        api=True marks authenticated API calls: rate-limited by quota, not
        by politeness, so the anti-bot delay is skipped (tiny jitter only).
        """
        lane = "api" if api else "html"
        if self.lane_tripped(lane):
            note_api(f"{self.site}/{lane}", "skipped")
            if lane not in self._announced:
                until = self._cooldown_until.get(lane)
                if until is not None:
                    log.warning("%s/%s: cooling off after earlier failures - "
                                "not contacting site until %s", self.site,
                                lane, until.astimezone().strftime("%H:%M"))
                else:
                    log.warning("%s/%s: %d consecutive failures - skipping "
                                "this channel for the rest of the run",
                                self.site, lane, self._streaks[lane])
                self._announced.add(lane)
            return None

        def do_request():
            # RE-CHECK the breaker: with parallel workers, many calls pass
            # the entry check above, then queue on the html lock - if the
            # lane tripped while they waited, they must NOT fire (this was
            # visible in the log as "request failed (4/3)...(10/3)" AFTER
            # a backoff was announced)
            if self.lane_tripped(lane):
                note_api(f"{self.site}/{lane}", "skipped")
                return None
            time.sleep(random.uniform(0.05, 0.2) if api
                       else self.delay * (0.5 + random.random()))
            try:
                r = self.session.get(
                    url, timeout=(30 if api else self.html_timeout),
                    **kwargs)
                r.raise_for_status()
                self._streaks[lane] = 0
                note_api(f"{self.site}/{lane}", "ok")
                return r
            except _HTTP_ERRORS as e:
                note_api(f"{self.site}/{lane}", "failed")
                self._streaks[lane] += 1
                log.warning("%s/%s: request failed (%d/%d) for %s (%s)",
                            self.site, lane, self._streaks[lane],
                            self.trip_after, redact_url(url), redact_text(e))
                if self._streaks[lane] == self.trip_after:
                    # persist so the every-30-min cron doesn't re-assault a
                    # site that's rejecting us (cross-run backoff)
                    self._persist_trip(
                        lane, "%d consecutive failures" % self._streaks[lane])
                return None

        if api:                     # quota-limited, safe to run concurrently
            return do_request()
        with self._html_lock:       # politeness: serial per site
            if not self._warmed and self.warmup_url:
                self._warmed = True  # once per session, success or not
                try:
                    self.session.get(self.warmup_url, timeout=20)
                    log.debug("%s: warmed up cookies via %s",
                              self.site, self.warmup_url)
                except Exception:
                    pass             # warm-up is best-effort
            r = do_request()
            if r is not None:
                self._save_cookies()
            return r

    def _post(self, url: str, api: bool = False,
              **kwargs) -> requests.Response | None:
        """POST counterpart to ``_get`` with the same breaker/telemetry."""
        lane = "api" if api else "html"
        if self.lane_tripped(lane):
            note_api(f"{self.site}/{lane}", "skipped")
            return None
        time.sleep(random.uniform(0.05, 0.2) if api
                   else self.delay * (0.5 + random.random()))
        try:
            r = self.session.post(
                url, timeout=(30 if api else self.html_timeout), **kwargs)
            r.raise_for_status()
            self._streaks[lane] = 0
            note_api(f"{self.site}/{lane}", "ok")
            return r
        except _HTTP_ERRORS as e:
            note_api(f"{self.site}/{lane}", "failed")
            self._streaks[lane] += 1
            log.warning("%s/%s: POST failed (%d/%d) for %s (%s)",
                        self.site, lane, self._streaks[lane],
                        self.trip_after, redact_url(url), redact_text(e))
            if self._streaks[lane] == self.trip_after:
                self._persist_trip(
                    lane, "%d consecutive failures" % self._streaks[lane])
            return None

    def search_auctions(self, query: str, max_results: int = 50):
        """Return list[Listing] of live auctions matching query."""
        raise NotImplementedError

    def search_sold(self, query: str, max_results: int = 60):
        """Return list[SoldComp] of recent sold items. Optional."""
        return []
