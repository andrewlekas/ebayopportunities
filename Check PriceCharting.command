#!/bin/bash
# Verify what PriceCharting's paid API actually returns for YOUR cards.
#
# The scanner now resolves each listing to ONE PriceCharting product instead
# of sending a search phrase and accepting whatever came back. This script
# shows you that resolution happening, card by card, so you can see whether
# it is landing the right product before you trust a Max Bid.
#
# It makes a handful of calls, paced at one per second. Nothing is written
# to the database and no credential is ever printed.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "No .venv yet - run 'Run Scan.command' once first."
  read -r -p "Press return to close..." _
  exit 1
fi
.venv/bin/python -B code/probe_pricecharting.py "$@"
echo
read -r -p "Press return to close..." _
