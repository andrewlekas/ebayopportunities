#!/bin/bash
# Download the price-guide CSVs so scans stop paying for API calls.
#
# Each file is a whole category - tens of thousands of products - fetched in
# one request. After this, most lookups are answered instantly from disk
# instead of at one call per second over the network.
#
# PriceCharting allows one CSV download every 10 minutes. The scanner's
# configured refresh target is weekly, so:
#   * this waits 10 minutes between files (a first run takes a while)
#   * anything downloaded in the last 168 hours is skipped
# Older files remain usable by the pricer; age only controls re-downloading.
#
# Safe to leave running. Scans work normally while it goes. Your token is
# read from config.yaml and is never printed or written anywhere.
#
#   Download Price Guides.command           only what's stale or missing
#   Download Price Guides.command --force   re-download everything
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "No .venv yet - run 'Run Scan.command' once first."
  read -r -p "Press return to close..." _
  exit 1
fi
.venv/bin/python -B code/fetch_guide_csv.py "$@"
echo
read -r -p "Press return to close..." _
