"""Download PriceCharting / SportsCardsPro price-guide CSVs.

Run from `Download Price Guides.command`, or:

    .venv/bin/python -B code/fetch_guide_csv.py
    .venv/bin/python -B code/fetch_guide_csv.py --force

Each download is a whole category in one request - tens of thousands of
products - which then answers lookups locally at zero latency and zero API
quota. The paid API is one call per second and is the dominant cost of a
full scan, so this is the difference between minutes and seconds.

Two limits shape how this behaves, both from PriceCharting's own docs:

  * CSV requests are limited to ONE EVERY TEN MINUTES. We therefore wait
    between downloads. A first run covering several categories takes a
    while; that is the API's rule, not our choice.
  * The upstream files may regenerate daily, but collectible guide prices do
    not need daily refreshes for this scanner. The default refresh target is
    once per week and can be changed with guide_csv.fresh_hours.

The token is read from config.yaml and is never printed, logged, or written
anywhere. Only the resulting .csv files are saved.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests                                            # noqa: E402

import main as scanner                                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = os.path.join(ROOT, "guide_csv")

PC = "https://www.pricecharting.com"
SCP = "https://www.sportscardspro.com"

# category slug -> (host, human name). A slug of "" is the host's default,
# which on PriceCharting is video games.
DEFAULT_GUIDES = [
    ("", PC, "video games"),
    ("pokemon-cards", PC, "Pokemon cards"),
    ("comic-books", PC, "comic books"),
    ("other-cards", PC, "other non-TCG cards"),
    ("baseball-cards", SCP, "baseball cards"),
    ("basketball-cards", SCP, "basketball cards"),
    ("football-cards", SCP, "football cards"),
    ("hockey-cards", SCP, "hockey cards"),
]

CSV_COOLDOWN_SECONDS = 610        # their documented 1-per-10-minutes, plus a nudge
FRESH_HOURS = 168                 # weekly refresh; older files remain usable


def _age_hours(path: str) -> float | None:
    if not os.path.isfile(path):
        return None
    delta = datetime.now(timezone.utc) - datetime.fromtimestamp(
        os.path.getmtime(path), tz=timezone.utc)
    return delta.total_seconds() / 3600.0


def _filename(host: str, slug: str) -> str:
    site = "sportscardspro" if "sportscardspro" in host else "pricecharting"
    return f"{site}--{slug or 'video-games'}.csv"


def download(host: str, slug: str, token: str, path: str) -> tuple[bool, str]:
    """Stream one guide CSV to disk. Returns (ok, message)."""
    params = {"t": token}
    if slug:
        params["category"] = slug
    tmp = path + ".part"
    try:
        with requests.get(f"{host}/price-guide/download-custom",
                          params=params, timeout=600, stream=True) as r:
            if r.status_code != 200:
                return False, f"HTTP {r.status_code}"
            head = ""
            written = 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if not chunk:
                        continue
                    if not head:
                        head = chunk[:200].decode("utf-8", "replace")
                        # An HTML page means the token was refused or the
                        # subscription does not cover this category.
                        if head.lstrip().lower().startswith(("<!doctype",
                                                             "<html")):
                            return False, ("got a web page, not a CSV - the "
                                           "token may not cover this guide")
                    fh.write(chunk)
                    written += len(chunk)
        if written == 0:
            return False, "empty response"
        if "product-name" not in head:
            return False, f"unexpected header: {head.splitlines()[0][:70]!r}"
        os.replace(tmp, path)
        return True, f"{written / 1_048_576:.1f} MB"
    except requests.RequestException as exc:
        return False, type(exc).__name__
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def main() -> int:
    force = "--force" in sys.argv[1:]
    config = scanner.load_config(os.path.join(ROOT, "config.yaml"))
    fresh_hours = float(
        (config.get("guide_csv") or {}).get("fresh_hours", FRESH_HOURS))
    token = ((config.get("api_keys", {}) or {}).get("pricecharting")
             or {}).get("token")
    if not token:
        print("No PriceCharting token in config.yaml - nothing to download.")
        return 1

    guides = config.get("guide_csv", {}).get("guides") or DEFAULT_GUIDES
    guides = [tuple(g) if not isinstance(g, dict)
              else (g.get("category", ""), g.get("host", PC), g.get("name", ""))
              for g in guides]
    os.makedirs(FOLDER, exist_ok=True)

    todo = []
    print("=" * 74)
    print("PRICE-GUIDE CSV DOWNLOAD")
    print("=" * 74)
    for slug, host, name in guides:
        path = os.path.join(FOLDER, _filename(host, slug))
        age = _age_hours(path)
        if age is not None and age < fresh_hours and not force:
            print(f"  fresh ({age:4.1f}h)  {name}")
        else:
            todo.append((slug, host, name, path))
            print(f"  QUEUED       {name}")

    if not todo:
        print()
        print(f"Everything is current (refresh target: {fresh_hours:g}h).")
        print("Older CSVs remain usable by the pricer; this only controls")
        print("when the download helper offers to replace them.")
        print("Use --force if you really want to.")
        return 0

    minutes = (len(todo) - 1) * (CSV_COOLDOWN_SECONDS / 60)
    print()
    print(f"{len(todo)} file(s) to fetch. PriceCharting allows one CSV every")
    print(f"10 minutes, so this will take about {minutes:.0f} minutes.")
    print("You can leave it running - scans work fine meanwhile.")
    print()

    ok = 0
    for i, (slug, host, name, path) in enumerate(todo):
        if i:
            print(f"  waiting {CSV_COOLDOWN_SECONDS // 60} min for the CSV "
                  "rate limit...")
            time.sleep(CSV_COOLDOWN_SECONDS)
        print(f"  downloading {name}...", flush=True)
        good, detail = download(host, slug, token, path)
        if good:
            ok += 1
            print(f"    saved  {os.path.basename(path)}  ({detail})")
        else:
            print(f"    FAILED  {detail}")
            if "token may not cover" in detail:
                print("    (that category may need its own subscription)")

    print()
    print(f"Downloaded {ok} of {len(todo)}.")
    print("These are used automatically on the next scan - no import step.")
    print("Run 'Check Price CSVs.command' to see what they cover.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
