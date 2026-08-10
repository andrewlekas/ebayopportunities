#!/bin/bash
# Double-click to capture what Japanese auction search actually returns.
#
# Context (2026-08-09): Buyee's search moved behind an AWS WAF JavaScript
# challenge - debug/buyee_last_failure.html IS the challenge page, so no
# parser can fix it. Plan B is to search Yahoo! Auctions Japan directly
# and keep Buyee only as the purchase link. This fetches ONE page of each
# so the new parser is written from real markup, not guesses.
cd "$(dirname "$0")"
.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, "code")
import main as scanner
from scrapers.yahoo_jp import YahooJpScraper
from urllib.parse import quote
cfg = scanner.load_config("config.yaml")
sc = YahooJpScraper(cfg)
q = "トップサン リザードン"          # Topsun Charizard - a real target
targets = [
    ("yahoo_direct.html",
     f"https://auctions.yahoo.co.jp/search/search?p={quote(q)}"),
    ("buyee_recheck.html",
     f"https://buyee.jp/item/search/query/{quote(q)}?translationType=98"),
]
import os
os.makedirs("debug", exist_ok=True)
for name, url in targets:
    r = sc._get(url, headers={"Accept-Language": "en"})
    if not r:
        print(f"  {name:<22} request FAILED")
        continue
    path = os.path.join("debug", name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(r.text[:3_000_000])
    waf = "awswaf" in r.text or "challenge" in r.text[:2000].lower()
    print(f"  {name:<22} {len(r.text):>9,} bytes"
          + ("   <- STILL A BOT CHALLENGE" if waf else "   looks like content"))
print("\nTell Claude when done - files are in debug/")
PY
echo
read -r -p "Press Enter to close..."
