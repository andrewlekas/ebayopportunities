#!/bin/bash
# Double-click when the scan says yahoo_jp found "no item cards".
# Fetches one Buyee search page exactly the way the scanner does and saves
# it to debug/buyee_last_failure.html so the parser can be fixed from the
# real markup. One request, nothing else.
cd "$(dirname "$0")"
.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, "code")
import main as scanner
from scrapers.yahoo_jp import YahooJpScraper, SEARCH_URL, translate_query
from urllib.parse import quote
cfg = scanner.load_config("config.yaml")
sc = YahooJpScraper(cfg)
q = translate_query("Topsun Charizard PSA 9")
r = sc._get(SEARCH_URL.format(q=quote(q)), headers={"Accept-Language": "en"})
if not r:
    print("request failed - see logs/scan.log")
    raise SystemExit(1)
sc._capture_failure.__func__  # ensure method exists
YahooJpScraper._captured_this_run = False
sc._capture_failure(r.text, q)
import re
cards = r.text.count("itemCard")
print(f"page saved. {len(r.text):,} bytes; 'itemCard' appears {cards} times.")
print("Tell Claude it is ready - the file is debug/buyee_last_failure.html")
PY
echo
read -r -p "Press Enter to close..."
