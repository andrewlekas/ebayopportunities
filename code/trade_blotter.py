"""Persistent opportunity-to-trade workflow backed by one canonical CSV.

The workbook is a read-only snapshot. User decisions and actual cash flows
live in ``trade_blotter/trade_blotter.csv`` so they survive every scan.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import yaml

from models import Opportunity
from quality import is_tradeable

log = logging.getLogger(__name__)

STATUSES = (
    "Discovered", "Verified", "Watching", "Bid/Offer Placed",
    "Won", "Lost", "Received", "Listed", "Sold", "Passed",
)

FIELDS = [
    "listing_key", "first_seen", "last_seen", "status", "verified",
    "site", "listing_type", "title", "query", "matched_queries", "url",
    "identity_key", "current_price", "shipping_quote", "auction_end",
    "fair_value", "confidence", "edge_now", "roi", "opportunity_score",
    "suggested_max_bid", "breakeven", "best_exit", "net_proceeds",
    "planned_bid_or_offer", "actual_purchase_price", "buyer_fees_paid",
    "shipping_paid", "tax_paid", "actual_landed_cost", "date_won",
    "date_received", "date_listed", "asking_price", "date_sold",
    "sale_proceeds", "realized_profit", "realized_roi", "holding_days",
    "notes",
]

EDITABLE_FIELDS = {
    "status", "verified", "planned_bid_or_offer", "actual_purchase_price",
    "buyer_fees_paid", "shipping_paid", "tax_paid", "date_won",
    "date_received", "date_listed", "asking_price", "date_sold",
    "sale_proceeds", "notes",
}

TERMINAL_STATUSES = {"Lost", "Sold", "Passed"}


def blotter_path(config: dict) -> str:
    cfg = config.get("trade_blotter", {}) or {}
    path = str(cfg.get(
        "file", "trade_blotter/trade_blotter.csv")).strip()
    if not os.path.isabs(path):
        path = os.path.join(config.get("_config_dir") or os.getcwd(), path)
    return os.path.abspath(path)


def _clean_url(url: str) -> str:
    try:
        parsed = urlsplit(url or "")
        return urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(),
             parsed.path.rstrip("/"), "", ""))
    except (TypeError, ValueError):
        return str(url or "").split("?", 1)[0].split("#", 1)[0]


def listing_key(opportunity: Opportunity) -> str:
    listing = opportunity.listing
    asset = "".join(str(
        listing.canonical_asset_id or "").lower().split())
    if asset:
        return f"asset:{asset}"
    site = str(listing.site or "unknown").lower()
    native = str(listing.listing_id or "").strip().lower()
    if native:
        return f"listing:{site}:{native}"
    clean_url = _clean_url(listing.url)
    if clean_url:
        return f"url:{site}:{clean_url.lower()}"
    fallback = "|".join([
        site, str(listing.title or "").strip().lower(),
        str(listing.current_price or 0),
    ])
    digest = hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:20]
    return f"anonymous:{digest}"


def _number(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _money(value) -> str:
    number = _number(value)
    return "" if number is None else f"{number:.2f}"


def _ratio(value) -> str:
    number = _number(value)
    return "" if number is None else f"{number:.6f}"


def _date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def derive(row: dict) -> dict:
    """Recalculate landed cost, realized return, and holding period."""
    purchase = _number(row.get("actual_purchase_price"))
    if purchase is not None:
        extras = sum(_number(row.get(field)) or 0.0 for field in (
            "buyer_fees_paid", "shipping_paid", "tax_paid"))
        landed = purchase + extras
        row["actual_landed_cost"] = f"{landed:.2f}"
    else:
        landed = None
        row["actual_landed_cost"] = ""

    proceeds = _number(row.get("sale_proceeds"))
    if landed is not None and proceeds is not None:
        profit = proceeds - landed
        row["realized_profit"] = f"{profit:.2f}"
        row["realized_roi"] = (
            f"{profit / landed:.6f}" if landed > 0 else "")
    else:
        row["realized_profit"] = ""
        row["realized_roi"] = ""

    start = _date(row.get("date_won"))
    end = _date(row.get("date_sold"))
    row["holding_days"] = (
        str(max(0, (end - start).days)) if start and end else "")
    return row


def _blank_row(row: dict | None = None) -> dict:
    source = row or {}
    return {field: str(source.get(field) or "") for field in FIELDS}


def _backup(path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.pre-schema-{stamp}.bak.csv"
    shutil.copy2(path, backup)
    return backup


def write_rows(path: str, rows: list[dict]) -> None:
    """Atomically write the canonical schema."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=directory,
        prefix=".trade-blotter-", suffix=".csv", delete=False)
    temp_path = handle.name
    try:
        with handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow(_blank_row(row))
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def load_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        write_rows(path, [])
        return []
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            original_fields = list(reader.fieldnames or [])
            rows = [_blank_row(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise RuntimeError(
            f"trade blotter unreadable at {path}: {exc}") from exc
    if original_fields != FIELDS:
        backup = _backup(path)
        write_rows(path, rows)
        log.warning("trade blotter schema upgraded; backup: %s", backup)
    for row in rows:
        derive(row)
    return rows


def ensure_file(config: dict) -> str:
    path = blotter_path(config)
    load_rows(path)
    return path


def _iso(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _opportunity_row(opportunity: Opportunity, config: dict,
                     now: str) -> dict:
    listing, valuation = opportunity.listing, opportunity.valuation
    try:
        from report import _bid_levels
        maximum, breakeven = _bid_levels(opportunity, config)
    except Exception:
        maximum = breakeven = None
    queries = list(listing.matched_queries or [])
    if listing.query and listing.query not in queries:
        queries.insert(0, listing.query)
    return _blank_row({
        "listing_key": listing_key(opportunity),
        "first_seen": now,
        "last_seen": now,
        "status": "Discovered",
        "verified": "no",
        "site": listing.site,
        "listing_type": listing.listing_type,
        "title": listing.title,
        "query": listing.query,
        "matched_queries": " | ".join(queries),
        "url": listing.url,
        "identity_key": valuation.identity_key,
        "current_price": _money(listing.current_price),
        "shipping_quote": _money(listing.shipping),
        "auction_end": _iso(listing.end_time),
        "fair_value": _money(valuation.fair_value),
        "confidence": _ratio(valuation.confidence),
        "edge_now": _money(valuation.edge_now),
        "roi": _ratio(valuation.roi),
        "opportunity_score": _ratio(valuation.opportunity_score),
        "suggested_max_bid": _money(maximum),
        "breakeven": _money(breakeven),
        "best_exit": valuation.resale_channel,
        "net_proceeds": _money(valuation.net_proceeds),
    })


def _eligible(opportunity: Opportunity, config: dict) -> bool:
    listing = opportunity.listing
    cfg = config.get("trade_blotter", {}) or {}
    if not is_tradeable(opportunity):
        return False
    if listing.discovery:
        return False
    if listing.grail and not cfg.get("include_grails", False):
        return False
    category = (listing.category or "").strip().lower()
    if category == "watches":
        return False
    return True


def _sort_rows(rows: list[dict]) -> list[dict]:
    status_rank = {status: index for index, status in enumerate(STATUSES)}
    newest_first = sorted(
        rows, key=lambda row: str(row.get("last_seen") or ""),
        reverse=True)
    return sorted(
        newest_first,
        key=lambda row: (
            row.get("status") in TERMINAL_STATUSES,
            status_rank.get(row.get("status"), len(STATUSES)),
        ))


def sync(opportunities: list[Opportunity], config: dict) -> list[dict]:
    """Upsert the run's best tradeable rows without touching user fields."""
    path = blotter_path(config)
    existing = load_rows(path)
    cfg = config.get("trade_blotter", {}) or {}
    if cfg.get("enabled", True) is False:
        return _sort_rows(existing)
    try:
        limit = max(0, int(cfg.get("auto_capture_top_n", 50)))
    except (TypeError, ValueError):
        limit = 50
    candidates = sorted(
        (row for row in opportunities if _eligible(row, config)),
        key=lambda row: row.valuation.opportunity_score,
        reverse=True)[:limit]
    now = datetime.now(timezone.utc).isoformat()
    by_key = {row.get("listing_key"): row for row in existing
              if row.get("listing_key")}
    added = updated = 0
    for opportunity in candidates:
        fresh = _opportunity_row(opportunity, config, now)
        key = fresh["listing_key"]
        prior = by_key.get(key)
        if prior is None:
            by_key[key] = fresh
            added += 1
            continue
        preserved = {field: prior.get(field, "")
                     for field in EDITABLE_FIELDS}
        first_seen = prior.get("first_seen") or fresh["first_seen"]
        prior.update(fresh)
        prior.update(preserved)
        prior["first_seen"] = first_seen
        derive(prior)
        updated += 1

    rows = _sort_rows([derive(_blank_row(row))
                       for row in by_key.values()])
    write_rows(path, rows)
    log.info(
        "trade blotter: %d new, %d refreshed, %d total -> %s",
        added, updated, len(rows), path)
    return rows


def _load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    config["_config_dir"] = os.path.dirname(os.path.abspath(path))
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Open the trade blotter CSV")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    config = _load_config(args.config)
    path = ensure_file(config)
    print(path)
    if args.open:
        subprocess.run(["open", path], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
