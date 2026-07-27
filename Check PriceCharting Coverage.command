#!/bin/bash
# Which of your categories can PriceCharting actually lead on?
#
# The scanner now lets PriceCharting SET the fair value whenever it resolves
# a listing to one specific product. That works beautifully where their
# catalogue is deep and not at all where it isn't - 1948 Bowman basketball
# returned 100 comic books and no Mikan.
#
# This walks vintage sports, modern sports, vintage Pokemon, sealed games,
# watches and comics, and tells you which ones the guide can carry and which
# have to fall back to sold comps.
#
# About 18 paced calls, one per second. Nothing is written to the database
# and no credential is ever printed.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "No .venv yet - run 'Run Scan.command' once first."
  read -r -p "Press return to close..." _
  exit 1
fi
.venv/bin/python -B code/probe_pricecharting.py --coverage
echo
read -r -p "Press return to close..." _
