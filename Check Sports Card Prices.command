#!/bin/bash
# Can we get sports card prices from SportsCardsPro?
#
# pricecharting.com's API carries TCG, video games, comics, Funko and LEGO -
# but NOT sports cards. Searching it for a 1952 Mantle returns Funko POPs and
# LEGO sets. That is why every sports row in your workbook falls back to
# eBay sold comps.
#
# SportsCardsPro.com is PriceCharting's OWN sister site for sports cards.
# Same company, same API shape, same field names, same grade ladder - their
# documentation even uses "Michael Jordan #57 | Basketball Cards 1986 Fleer"
# as its worked example.
#
# The only open question is whether your existing PriceCharting token is
# accepted there, or whether it needs its own subscription. This answers
# that in one call, then tries the six sports cards that failed.
#
# About 7 paced calls. Nothing is written to the database and no credential
# is ever printed.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "No .venv yet - run 'Run Scan.command' once first."
  read -r -p "Press return to close..." _
  exit 1
fi
.venv/bin/python -B code/probe_pricecharting.py --sports
echo
read -r -p "Press return to close..." _
