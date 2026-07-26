"""Inventory & P&L: the trading-desk view of what Andrew actually owns.

He logs buys and sells in `portfolio.csv` (edits it in Excel/Numbers, saves
as CSV). Every full scan marks open positions to market using the scanner's
own fair values (this run's, falling back to the latest recorded
fair_history) and writes a Portfolio tab into the report:

    unrealized P&L per open position (net of the same sell-side costs the
    scanner uses - vault 7% at >= $500, else 13.25%), realized P&L on
    closed positions, holding days, and annualized return (CAGR).

portfolio.csv columns (header row required, extra columns ignored):
    date_bought  - YYYY-MM-DD
    description  - free text, whatever helps him recognize it
    query        - matching watchlist query for mark-to-market (optional
                   but recommended; without it the position stays unmarked)
    cost_basis   - all-in cost in dollars (price + tax + shipping + fees)
    date_sold    - YYYY-MM-DD, blank while still held
    sale_proceeds- net dollars received, blank while still held
    notes        - free text
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

CSV_FILE = "portfolio.csv"
CSV_HEADER = ["date_bought", "description", "query", "cost_basis",
              "date_sold", "sale_proceeds", "notes"]


def _num(x) -> float | None:
    try:
        v = float(str(x).replace("$", "").replace(",", "").strip())
        return v
    except (TypeError, ValueError):
        return None


def _date(x):
    try:
        return datetime.strptime(str(x).strip(), "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def ensure_template(directory: str = ".") -> None:
    """Create an empty portfolio.csv with headers if none exists."""
    path = os.path.join(directory, CSV_FILE)
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(CSV_HEADER)
        log.info("portfolio: created empty %s - log buys there", CSV_FILE)


def build_rows(config: dict, fair_by_query: dict[str, float],
               directory: str = ".") -> list[dict] | None:
    """Positions with P&L math, or None if no portfolio file/rows."""
    path = os.path.join(directory, CSV_FILE)
    if not os.path.exists(path):
        return None
    algo = config.get("algorithm", {})
    sell_fee = algo.get("resale_fee_rate", 0.1325)
    vault = algo.get("psa_vault") or {}
    vault_on = bool(vault.get("enabled", False))
    vault_min = vault.get("min_price", 500.0)
    vault_fee = vault.get("sell_fee_rate", 0.07)
    now = datetime.now(timezone.utc)

    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            cost = _num(r.get("cost_basis"))
            if cost is None or cost <= 0:
                continue
            bought = _date(r.get("date_bought"))
            sold = _date(r.get("date_sold"))
            proceeds = _num(r.get("sale_proceeds"))
            query = (r.get("query") or "").strip()
            desc = (r.get("description") or query or "?").strip()

            row = {"description": desc, "query": query, "cost": cost,
                   "bought": bought, "sold": sold, "notes":
                   (r.get("notes") or "").strip()}
            end = sold or now
            days = max((end - bought).days, 1) if bought else None
            row["days"] = days

            if sold and proceeds is not None:          # closed position
                row["status"] = "SOLD"
                row["value"] = proceeds
                row["pnl"] = proceeds - cost
            else:                                       # open: mark to market
                row["status"] = "OPEN"
                fair = fair_by_query.get(query.lower()) if query else None
                if fair:
                    fee = (vault_fee if (vault_on and fair >= vault_min)
                           else sell_fee)
                    row["value"] = fair * (1 - fee)     # net liquidation value
                    row["pnl"] = row["value"] - cost
                else:
                    row["value"] = None                 # unmarked
                    row["pnl"] = None

            pnl, days_h = row["pnl"], row["days"]
            if pnl is not None and days_h and row["value"] and row["value"] > 0:
                ratio = row["value"] / cost
                row["cagr"] = (ratio ** (365.0 / days_h) - 1
                               if 0 < ratio < 100 else None)
            else:
                row["cagr"] = None
            out.append(row)
    return out or None


def latest_fairs(conn) -> dict[str, float]:
    """query(lower) -> most recently recorded fair value."""
    rows = conn.execute(
        """SELECT query, fair FROM fair_history
           WHERE rowid IN (SELECT MAX(rowid) FROM fair_history
                           GROUP BY query)""").fetchall()
    return {q.lower(): f for q, f in rows if f}
