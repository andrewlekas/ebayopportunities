"""Report what the local price-guide CSVs cover, and what they don't.

Run from `Check Price CSVs.command`. Makes no network calls.

Answers two questions:
  1. Did my CSV files load, and how many products do they hold?
  2. Which of my watchlist queries would still fall through to the paid API?

The second is the useful one - it tells you exactly which sets are worth
downloading next, rather than guessing.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main as scanner                                     # noqa: E402
from valuation.guide_csv import load_index                  # noqa: E402
from valuation.identity import identity_of                  # noqa: E402
from targets import configured_scan_entries                 # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    config = scanner.load_config(os.path.join(ROOT, "config.yaml"))
    index = load_index(ROOT)

    print("=" * 74)
    print("LOCAL PRICE-GUIDE CSVs")
    print("=" * 74)
    if not len(index):
        print("  No CSV rows loaded.")
        print(f"  Drop .csv files into:  guide_csv/")
        print("  See guide_csv/README.txt for where to get them.")
        print()
        print("  Everything still works - every lookup just uses the paid")
        print("  API, at one call per second.")
        return 0

    print(f"  {len(index):,} products loaded from {len(index.files)} file(s):")
    for name in index.files:
        print(f"    - {name}")

    # Which watchlist queries can the CSVs already answer?
    watch = [w.get("query") if isinstance(w, dict) else str(w)
             for w in configured_scan_entries(config)]
    watch = [w for w in watch if w]
    if not watch:
        return 0

    covered, missing = [], []
    for query in watch:
        ident = identity_of(query)
        rows = index.search(ident.guide_query() or query, limit=20)
        best = 0.0
        for row in rows:
            best = max(best, ident.score_candidate(row.get("product-name", ""),
                                                   row.get("console-name", "")))
        (covered if best >= 0.62 else missing).append((best, query))

    print()
    print("=" * 74)
    print("WATCHLIST COVERAGE")
    print("=" * 74)
    print(f"  {len(covered)} of {len(watch)} watchlist queries can be answered "
          "locally (no API call)")
    if missing:
        print()
        print("  Still falling through to the paid API - download these sets")
        print("  next if you want them faster:")
        for best, query in sorted(missing, reverse=True)[:25]:
            print(f"    {best:4.0%}  {query[:60]}")
        if len(missing) > 25:
            print(f"    ... and {len(missing) - 25} more")
    print()
    print("Note: watchlist queries are broad by design, so a low score here")
    print("is normal. What matters at scan time is the individual LISTING,")
    print("which is far more specific than the query that found it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
