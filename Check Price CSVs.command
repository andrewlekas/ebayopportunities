#!/bin/bash
# What do my downloaded price-guide CSVs cover, and what still costs API calls?
#
# Drop .csv files into the guide_csv/ folder (see the README in there for
# where to get them). This tells you what loaded and which of your watchlist
# queries would still fall through to the paid one-call-per-second API, so
# you know which sets are worth downloading next.
#
# Makes no network calls.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "No .venv yet - run 'Run Scan.command' once first."
  read -r -p "Press return to close..." _
  exit 1
fi
.venv/bin/python -B code/check_guide_csv.py
echo
read -r -p "Press return to close..." _
