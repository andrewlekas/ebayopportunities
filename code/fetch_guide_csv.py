"""Download PriceCharting / SportsCardsPro price-guide CSVs.

Run from `Download Price Guides.command`, or:

    .venv/bin/python -B code/fetch_guide_csv.py
    .venv/bin/python -B code/fetch_guide_csv.py --force

Each download is a whole category in one request - tens of thousands of
products - which then answers lookups locally at zero latency and zero API
quota. The paid API is one call per second and is the dominant cost of a
full scan, so this is the difference between minutes and seconds.

Two limits shape how this behaves, both from PriceCharting's own docs:

  * CSV requests are limited to ONE EVERY TEN MINUTES. We wait 15 by
    default for headroom (guide_csv.cooldown_seconds). A first run covering
    several categories takes a while; that is the provider's rule.
    NOTE: not every refusal is a timing problem. Diagnosing the 2026-07-28
    SportsCardsPro 403s took three wrong turns, so the record is:
      * NOT a rate limit - all four PriceCharting categories succeeded at
        ~10-minute spacing on the same run;
      * NOT a subscription boundary - the account holds SportsCardsPro
        Legendary, which includes Download Price Lists;
      * NOT a wrong token - byte-identical to the working browser URL;
      * PARTLY a wrong parameter - that host selects sets by `console-uids`,
        not catalogues by `category`, so the original call was malformed;
      * ULTIMATELY a bot check. With the URL corrected the endpoint returns
        a "Just a moment..." interstitial to a plain HTTP client. The fix is
        the one scrapers/base.py already uses for eBay and Goldin: fetch
        through curl_cffi with a real Chrome TLS/HTTP2 fingerprint instead
        of bare `requests`. Sending a Chrome User-Agent WITHOUT that
        handshake is worse than sending nothing, because the mismatch is
        itself the signal. If curl_cffi is missing we fall back to plain
        requests with no spoofed headers.
    Refusals that waiting cannot fix are skipped immediately rather than
    costing 15 minutes each.
  * The upstream files may regenerate daily, but collectible guide prices do
    not need daily refreshes for this scanner. The default refresh target is
    once per week and can be changed with guide_csv.fresh_hours.

The token is read from config.yaml and is never printed, logged, or written
anywhere. Only the resulting .csv files are saved.
"""
from __future__ import annotations

import os
import re
import sys
import time
from contextlib import closing
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests                                            # noqa: E402

import main as scanner                                     # noqa: E402
from security import redact_text                            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = os.path.join(ROOT, "guide_csv")

PC = "https://www.pricecharting.com"
SCP = "https://www.sportscardspro.com"

# category slug -> (host, human name). A slug of "" is the host's default,
# which on PriceCharting is video games.
#
# PriceCharting only. SportsCardsPro uses `console-uids` per SET rather than
# `category` per catalogue, so its entries live in config.yaml under
# guide_csv.guides where the uids can be recorded as they are collected:
#
#   guide_csv:
#     guides:
#       - name: 1986 Fleer Basketball
#         host: https://www.sportscardspro.com
#         console_uids: G155
#
# Listing SportsCardsPro categories here would send `category=` to a host
# that does not accept it and produce a 403 on every run.
DEFAULT_GUIDES = [
    ("", PC, "video games"),
    ("pokemon-cards", PC, "Pokemon cards"),
    ("comic-books", PC, "comic books"),
    ("other-cards", PC, "other non-TCG cards"),
]

# Their documented limit is one CSV every ten minutes. 2026-07-28 evidence:
# four PriceCharting categories downloaded cleanly at 10m13s / 11m08s / 10m31s
# spacing, so ten minutes IS sufficient for that host. The default is now 15
# minutes purely for headroom; override with guide_csv.cooldown_seconds.
CSV_COOLDOWN_SECONDS = 900
FRESH_HOURS = 168                 # weekly refresh; older files remain usable

# Why a download failed decides what to do next. Sleeping fifteen minutes
# between files that are all going to be refused for the same reason wastes
# an hour and teaches us nothing.
KIND_OK = "ok"
KIND_RATE = "rate limit"
KIND_NOT_COVERED = "not covered by this subscription"
KIND_CHALLENGE = "bot challenge"
KIND_HTTP = "http error"
KIND_NETWORK = "network error"
KIND_PAYLOAD = "unexpected payload"

# A Cloudflare interstitial is NOT a subscription problem, and saying so
# sent this investigation down the wrong path twice. sportscardspro.com sits
# behind bot protection that a browser clears and a plain HTTP client does
# not - the same wall that already keeps 130point disabled.
_CHALLENGE_MARKERS = (
    "just a moment", "challenges.cloudflare.com", "cf-browser-verification",
    "cf_chl", "checking your browser", "enable javascript and cookies",
    "attention required", "ddos protection",
)


def _looks_like_challenge(text: str) -> bool:
    low = (text or "").casefold()
    return any(marker in low for marker in _CHALLENGE_MARKERS)


def _age_hours(path: str) -> float | None:
    if not os.path.isfile(path):
        return None
    delta = datetime.now(timezone.utc) - datetime.fromtimestamp(
        os.path.getmtime(path), tz=timezone.utc)
    return delta.total_seconds() / 3600.0


# A Chrome User-Agent on top of Python's TLS handshake is worse than no
# header at all: the mismatch between "I am Chrome" and a non-Chrome TLS
# fingerprint is itself a bot signal. These headers are therefore only sent
# alongside curl_cffi, which makes the handshake match the claim.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/csv,application/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

try:                                                        # optional
    from curl_cffi import requests as curl_requests
except ImportError:                                         # pragma: no cover
    curl_requests = None


def _http_get(url: str, params: dict, impersonate: str = "chrome"):
    """GET using the same client every other source in this project uses.

    scrapers/base.py has reached for curl_cffi since 2026-07 because plain
    `requests` is fingerprinted and blocked by eBay and others. This
    endpoint was the one place still using bare requests, and it is the one
    place returning a Cloudflare interstitial - so it gets the same
    treatment rather than a special case.
    """
    if curl_requests is not None:
        try:
            return curl_requests.get(url, params=params,
                                     impersonate=impersonate, timeout=600)
        except Exception:                                   # noqa: BLE001
            pass          # fall through to plain requests below
    return requests.get(url, params=params, timeout=600)


# These tokens are 40 hex characters. security.redact_text only strips
# credential-shaped QUERY parameters, so a page that echoes the token in
# body text would sail straight through it and onto the screen.
_HEXTOKEN_RE = re.compile(r"\b[0-9a-f]{32,64}\b", re.I)


def _reason_from(response) -> str:
    """A short, credential-safe explanation from an error response body."""
    try:
        snippet = response.content[:600].decode("utf-8", "replace")
    except Exception:                                      # noqa: BLE001
        return "no response body"
    text = re.sub(r"<[^>]+>", " ", snippet)          # strip tags
    text = re.sub(r"\s+", " ", text).strip()
    text = _HEXTOKEN_RE.sub("<redacted>", redact_text(text))
    return text[:220] or "empty response body"


def _host_label(host: str) -> str:
    return "SportsCardsPro" if "sportscardspro" in host else "PriceCharting"


def _site(host: str) -> str:
    return "sportscardspro" if "sportscardspro" in host else "pricecharting"


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).casefold()).strip("-")


def _filename(host: str, slug: str) -> str:
    """Filename carries PROVENANCE, not just a label.

    guide_csv infers the guide host from the `sportscardspro--` prefix, and
    that provenance is what stops a PriceCharting file pricing a Sports
    Card. Never save a sports guide under a pricecharting-- name.
    """
    return f"{_site(host)}--{slug or 'video-games'}.csv"


def _normalise_guides(raw) -> list[dict]:
    """Accept legacy (slug, host, name) tuples and explicit dict entries.

    The two sites take DIFFERENT selectors on the same endpoint:

        PriceCharting    /price-guide/download-custom?t=..&category=pokemon-cards
        SportsCardsPro   /price-guide/download-custom?t=..&console-uids=G155

    Sending `category=baseball-cards` to SportsCardsPro returns HTTP 403 even
    for a Legendary subscriber, which looked like a subscription problem and
    was not. Correcting the selector then revealed the real wall: that host
    is behind a Cloudflare challenge, so these entries are expected to fail
    from a script and be satisfied by a browser download instead.
    """
    out: list[dict] = []
    for entry in raw or []:
        if isinstance(entry, dict):
            host = str(entry.get("host") or PC).rstrip("/")
            name = str(entry.get("name") or entry.get("category")
                       or entry.get("console_uids") or "guide")
            uids = entry.get("console_uids") or entry.get("console-uids")
            if isinstance(uids, (list, tuple)):
                uids = ",".join(str(u).strip() for u in uids if str(u).strip())
            out.append({
                "name": name,
                "host": host,
                "category": (str(entry.get("category")).strip()
                             if entry.get("category") else None),
                "console_uids": str(uids).strip() if uids else None,
                "slug": str(entry.get("filename")
                            or entry.get("category")
                            or _slugify(name) or "guide"),
            })
            continue
        slug, host, name = entry
        out.append({"name": name, "host": str(host).rstrip("/"),
                    "category": slug or None, "console_uids": None,
                    "slug": slug or "video-games"})
    return out


def download(host: str, slug: str, token: str, path: str,
             console_uids: str | None = None) -> tuple[bool, str, str]:
    """Stream one guide CSV to disk. Returns (ok, message, kind).

    `kind` is the difference between "wait and try again" and "this will
    never work". On 2026-07-28 all four SportsCardsPro categories failed
    while all four PriceCharting ones succeeded, and the old return shape
    could not express that distinction - so the run slept ten minutes
    between each doomed attempt.
    """
    params = {"t": token}
    # SportsCardsPro selects sets by uid; PriceCharting selects by category.
    # Sending the wrong one to the wrong host is a 403, not a rate limit.
    if console_uids:
        params["console-uids"] = console_uids
    elif slug:
        params["category"] = slug
    tmp = path + ".part"
    try:
        with closing(_http_get(f"{host}/price-guide/download-custom",
                               params)) as r:
            if r.status_code == 429:
                return False, "HTTP 429 (too many requests)", KIND_RATE
            if r.status_code in (401, 402, 403):
                # The status alone does not say WHY. These endpoints return a
                # short HTML page explaining it ("log in", "subscription
                # required", "invalid token"), and reading it is the
                # difference between a fix and another guess. Redacted, so a
                # page echoing the token cannot leak it.
                reason = _reason_from(r)
                if _looks_like_challenge(reason):
                    return (False, "blocked by a Cloudflare bot challenge",
                            KIND_CHALLENGE)
                return (False, f"HTTP {r.status_code} - {reason}",
                        KIND_NOT_COVERED)
            if r.status_code != 200:
                return False, f"HTTP {r.status_code}", KIND_HTTP
            head = ""
            written = 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if not chunk:
                        continue
                    if not head:
                        head = chunk[:400].decode("utf-8", "replace")
                        # An HTML page means the token was refused or the
                        # subscription does not cover this category. Some
                        # providers also serve a 200 page saying "slow down".
                        if head.lstrip().lower().startswith(("<!doctype",
                                                             "<html")):
                            low = head.lower()
                            if any(w in low for w in ("too many", "rate limit",
                                                      "try again", "wait")):
                                return (False, "rate-limit page, not a CSV",
                                        KIND_RATE)
                            return (False,
                                    "got a web page, not a CSV - this "
                                    "subscription may not cover this guide",
                                    KIND_NOT_COVERED)
                    fh.write(chunk)
                    written += len(chunk)
        if written == 0:
            return False, "empty response", KIND_PAYLOAD
        if "product-name" not in head:
            return (False, f"unexpected header: {head.splitlines()[0][:70]!r}",
                    KIND_PAYLOAD)
        os.replace(tmp, path)
        return True, f"{written / 1_048_576:.1f} MB", KIND_OK
    except requests.RequestException as exc:
        return False, type(exc).__name__, KIND_NETWORK
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def diagnose(host: str, token: str, console_uids: str | None,
             category: str | None) -> int:
    """Try one download several ways and report what each attempt returns.

    Written because this 403 was mis-diagnosed three times. Two variables
    were changed in the same edit - a browser User-Agent AND reading the
    error body - so "Cloudflare" could equally have meant "Cloudflare was
    always there" or "claiming to be Chrome without Chrome's TLS handshake
    is itself the bot signal". One run, both answers.

    The SportsCardsPro *API* already works from plain requests on this same
    host, so a blanket bot wall is not consistent with the evidence.
    """
    attempts = [
        ("plain client, no custom headers", None, False),
        ("browser User-Agent only", BROWSER_HEADERS, False),
        ("curl_cffi Chrome TLS impersonation", None, True),
    ]
    params = {"t": token}
    if console_uids:
        params["console-uids"] = console_uids
    elif category:
        params["category"] = category

    shown = dict(params, t="<redacted>")
    print("=" * 74)
    print(f"DIAGNOSE  {_host_label(host)}  {shown}")
    print("=" * 74)

    for label, headers, use_curl in attempts:
        try:
            if use_curl:
                try:
                    from curl_cffi import requests as creq
                except ImportError:
                    print(f"  {label:<38} curl_cffi not installed - skipped")
                    continue
                r = creq.get(f"{host}/price-guide/download-custom",
                             params=params, impersonate="chrome", timeout=60)
            else:
                r = requests.get(f"{host}/price-guide/download-custom",
                                 params=params, headers=headers, timeout=60)
        except Exception as exc:                            # noqa: BLE001
            print(f"  {label:<38} ERROR {type(exc).__name__}")
            continue

        body = getattr(r, "content", b"")[:600]
        text = _reason_from(type("R", (), {"content": body})())
        if r.status_code == 200 and "product-name" in body.decode(
                "utf-8", "replace"):
            verdict = "CSV RECEIVED - this variant works"
        elif _looks_like_challenge(text):
            verdict = "Cloudflare challenge"
        else:
            verdict = text[:90]
        print(f"  {label:<38} HTTP {r.status_code}  {verdict}")

    print()
    print("Reading the results:")
    print("  * plain works, browser-UA challenged -> the fake Chrome header")
    print("    was the trigger; remove it.")
    print("  * all challenged, curl_cffi works    -> TLS fingerprinting.")
    print("  * all challenged incl. curl_cffi     -> a real wall; use the")
    print("    browser download, or ask support to allow the endpoint.")
    print("  * a non-Cloudflare message           -> read it literally; the")
    print("    parameter or uid is probably wrong.")
    return 0


def main() -> int:
    force = "--force" in sys.argv[1:]
    if "--diagnose" in sys.argv[1:]:
        cfg = scanner.load_config(os.path.join(ROOT, "config.yaml"))
        tok = ((cfg.get("api_keys", {}) or {}).get("pricecharting")
               or {}).get("token")
        if not tok:
            print("No PriceCharting token in config.yaml.")
            return 1
        rc = diagnose(SCP, tok, "G155", None)
        print()
        # The control: the same endpoint on the host that already works.
        diagnose(PC, tok, None, "comic-books")
        return rc
    config = scanner.load_config(os.path.join(ROOT, "config.yaml"))
    guide_cfg = config.get("guide_csv") or {}
    fresh_hours = float(guide_cfg.get("fresh_hours", FRESH_HOURS))
    cooldown = float(guide_cfg.get("cooldown_seconds", CSV_COOLDOWN_SECONDS))
    token = ((config.get("api_keys", {}) or {}).get("pricecharting")
             or {}).get("token")
    if not token:
        print("No PriceCharting token in config.yaml - nothing to download.")
        return 1

    # `guides` REPLACES the defaults (full control); `extra_guides` APPENDS
    # to them, which is what you want for adding SportsCardsPro sets without
    # having to restate every PriceCharting category and risk dropping one.
    guides = _normalise_guides(
        (guide_cfg.get("guides") or DEFAULT_GUIDES)
        + list(guide_cfg.get("extra_guides") or []))
    os.makedirs(FOLDER, exist_ok=True)

    todo = []
    print("=" * 74)
    print("PRICE-GUIDE CSV DOWNLOAD")
    print("=" * 74)
    for g in guides:
        path = os.path.join(FOLDER, _filename(g["host"], g["slug"]))
        g["path"] = path
        age = _age_hours(path)
        if age is not None and age < fresh_hours and not force:
            print(f"  fresh ({age:4.1f}h)  {g['name']}")
        else:
            todo.append(g)
            print(f"  QUEUED       {g['name']}")

    if not todo:
        print()
        print(f"Everything is current (refresh target: {fresh_hours:g}h).")
        print("Older CSVs remain usable by the pricer; this only controls")
        print("when the download helper offers to replace them.")
        print("Use --force if you really want to.")
        return 0

    minutes = (len(todo) - 1) * (cooldown / 60)
    print()
    print(f"{len(todo)} file(s) to fetch, waiting {cooldown / 60:.0f} min "
          "between each for the")
    print(f"provider's CSV rate limit - about {minutes:.0f} minutes in total.")
    print("You can leave it running - scans work fine meanwhile.")
    print()

    ok = 0
    failures: list[tuple[str, str, str]] = []
    # A host that refuses us for a subscription reason will refuse every one
    # of its categories. Recording that lets the rest of its files fail fast
    # instead of costing a quarter of an hour each.
    blocked_hosts: dict[str, tuple[str, str]] = {}
    pending = list(todo)
    waited = False
    while pending:
        g = pending.pop(0)
        slug, host, name, path = (g["category"] or "", g["host"], g["name"],
                                  g["path"])
        uids = g["console_uids"]
        if host in blocked_hosts:
            kind, why = blocked_hosts[host]
            print(f"  skipping {name} - {_host_label(host)} already refused "
                  f"this request ({why})")
            failures.append((name, why, kind))
            continue
        if waited:
            print(f"  waiting {cooldown / 60:.0f} min for the CSV rate "
                  "limit...", flush=True)
            time.sleep(cooldown)
        print(f"  downloading {name} from {_host_label(host)}...", flush=True)
        good, detail, kind = download(host, slug, token, path,
                                      console_uids=uids)
        waited = True
        if good:
            ok += 1
            print(f"    saved  {os.path.basename(path)}  ({detail})")
            continue
        if kind == KIND_RATE:
            # Genuinely rate limited: one longer wait, then one retry. Losing
            # the file to a timing problem is the one failure worth paying
            # for, because the alternative is another whole run.
            print(f"    rate limited ({detail}) - waiting "
                  f"{cooldown / 60:.0f} more min and retrying once")
            time.sleep(cooldown)
            good, detail, kind = download(host, slug, token, path,
                                          console_uids=uids)
            if good:
                ok += 1
                print(f"    saved  {os.path.basename(path)}  ({detail})")
                continue
        print(f"    FAILED  {detail}")
        failures.append((name, detail, kind))
        if kind in (KIND_NOT_COVERED, KIND_CHALLENGE):
            # Both refuse every category on that host, so stop paying 15
            # minutes each to be told the same thing.
            blocked_hosts[host] = (kind, detail)

    print()
    print(f"Downloaded {ok} of {len(todo)}.")
    if failures:
        print()
        print("Failures:")
        for name, detail, kind in failures:
            print(f"  {name:<24} {kind}: {detail}")
        if any(k == KIND_CHALLENGE for _n, _d, k in failures):
            print()
            print("A bot challenge is NOT a subscription problem and not a")
            print("timing problem - your subscription and token are fine.")
            print()
            if curl_requests is None:
                print("curl_cffi is NOT installed, so this request could only")
                print("go out with Python's own TLS fingerprint, which these")
                print("sites reject. Install it and this may just work:")
                print()
                print("  .venv/bin/pip install curl_cffi")
                print()
                print("Otherwise, download in the browser:")
            else:
                print("This already retried with a real Chrome TLS/HTTP2")
                print("fingerprint (curl_cffi, the same client used for eBay)")
                print("and was still challenged, so it is not a fingerprint")
                print("problem - the endpoint wants a cleared browser session.")
                print()
                print("Download those files in the browser instead - a few")
                print("clicks, and they only need refreshing weekly:")
            print()
            print("  1. open the set page while logged in")
            print("  2. click 'Download Price List'")
            print(f"  3. save it into {os.path.basename(FOLDER)}/ RENAMED to")
            print("     start with 'sportscardspro--', e.g.")
            print("     sportscardspro--1986-fleer-basketball.csv")
            print()
            print("The filename prefix is not cosmetic: it is how the pricer")
            print("knows the rows may value Sports Cards. A sports guide")
            print("saved under any other name will be ignored for them.")
            print()
            print("To see exactly what the host objects to, double-click")
            print("'Diagnose Guide Download.command' - it tests three")
            print("request styles against both hosts and prints the answer.")
        if any(k == KIND_NOT_COVERED for _n, _d, k in failures):
            print()
            print("A 'not covered' failure is not a timing problem - waiting")
            print("longer will not help. Check the Subscriptions page on the")
            print("refusing site, or fetch those prices through the paid API")
            print("instead (the scanner already falls back).")
    print()
    print("Downloaded files are used automatically on the next scan.")
    print("Run 'Check Price CSVs.command' to see what they cover.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
