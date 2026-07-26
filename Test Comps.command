#!/bin/bash
# Quick live test of the sold-comps sources (130point + eBay sold pages).
# Runs ONE query through the real scraper code and prints what came back.
# Safe to run anytime; ~30 seconds. Results also land in
# "test results/comps_test_result.txt" so Claude can read them.
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Run 'Run Scan.command' once first to set up."
    read -r -p "Press Enter to close..."
    exit 1
fi

# make sure new dependencies (e.g. curl_cffi) are present
.venv/bin/pip install --quiet -r setup/requirements.txt

.venv/bin/python -u - <<'EOF' 2>&1 | tee "test results/comps_test_result.txt"
import logging, sys
sys.path.insert(0, "code")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
import yaml
config = yaml.safe_load(open("config.yaml"))
query = "charizard base set psa 10"

print(f"\n=== eBay sold pages: '{query}' ===")
from scrapers.ebay import EbayScraper
comps = EbayScraper(config).search_sold(query)
for c in comps[:10]:
    print(f"  ${c.price:>9,.2f}  {c.title[:60]}")
print(f"  -> {len(comps)} sold comps from eBay")

print(f"\n=== 130point: '{query}' ===")
from scrapers.point130 import Point130Scraper
comps = Point130Scraper(config).search_sold(query)
for c in comps[:10]:
    print(f"  ${c.price:>9,.2f}  {c.sold_date:%Y-%m-%d}  {c.title[:60]}")
print(f"  -> {len(comps)} sold comps from 130point")

print("\neBay showing results = the main comps pipeline is healthy.")
print("130point 403s are a known issue (Cloudflare) - fix planned with")
print("Claude in the next Chrome session.")
EOF

read -r -p "Press Enter to close..."
