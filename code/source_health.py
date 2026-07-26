"""Persistent source-readiness snapshots for each scanner run."""
from __future__ import annotations

from datetime import datetime, timezone

import db as histdb
from scrapers.base import api_snapshot


def _status(counts: dict[str, int]) -> str:
    ok = counts.get("ok", 0)
    failed = counts.get("failed", 0)
    skipped = counts.get("skipped", 0)
    if skipped:
        return "cooling"
    if failed and ok:
        return "degraded"
    if failed:
        return "failing"
    if ok:
        return "healthy"
    return "idle"


def _configured_sources(config: dict) -> dict[str, tuple[bool, str]]:
    sites = {str(s) for s in (config.get("sites") or ["ebay"])}
    scraping = config.get("scraping", {}) or {}
    keys = config.get("api_keys", {}) or {}
    return {
        "ebay/listings": (
            "ebay" in sites,
            "Browse API with HTML fallback"),
        "ebay/comps": (
            "ebay" in sites and scraping.get("use_html_comps", True),
            "sold-listing HTML"),
        "130point/comps": (
            bool(scraping.get("use_130point", True)),
            "sold-comps HTML"),
        "yahoo_jp/listings": (
            "yahoo_jp" in sites,
            "Buyee/Yahoo Japan listings"),
        "goldin/listings": (
            "goldin" in sites, "Goldin listings"),
        "heritage/listings": (
            "heritage" in sites, "Heritage listings"),
        "fanatics_collect/listings": (
            "fanatics_collect" in sites,
            "Fanatics Collect listings"),
        "pricecharting/guide": (
            bool((keys.get("pricecharting") or {}).get("token")),
            "PriceCharting guide"),
        "pokemontcg/guide": (
            bool((keys.get("pokemontcg") or {}).get("api_key")),
            "PokemonTCG.io guide"),
    }


def _endpoint_matches(source: str, endpoint: str) -> bool:
    rules = {
        "ebay/listings": ("ebay/api", "ebay/oauth"),
        "ebay/comps": ("ebay/html",),
        "130point/comps": ("130point/html",),
        "yahoo_jp/listings": ("yahoo_jp/html",),
        "goldin/listings": ("goldin/html",),
        "heritage/listings": ("heritage/html",),
        "fanatics_collect/listings": (
            "fanatics_collect/api", "fanatics_collect/html"),
        "pricecharting/guide": ("pricecharting",),
        "pokemontcg/guide": ("pokemontcg.io",),
    }
    return endpoint in rules.get(source, ())


def capture(config: dict, mode: str) -> list[dict]:
    """Build, persist and return a source-readiness snapshot."""
    now = datetime.now(timezone.utc)
    run_at = now.isoformat()
    stats = api_snapshot()
    rows = []
    for source, (enabled, description) in _configured_sources(config).items():
        counts = {"ok": 0, "failed": 0, "skipped": 0}
        endpoints = []
        for endpoint, values in stats.items():
            if _endpoint_matches(source, endpoint):
                endpoints.append(endpoint)
                for outcome in counts:
                    counts[outcome] += int(values.get(outcome, 0))
        if not enabled:
            status = "disabled"
            detail = f"{description}; disabled in config"
        else:
            status = _status(counts)
            detail = (f"{description}; "
                      + (", ".join(sorted(endpoints))
                         if endpoints else "no network call needed this run"))
        rows.append({
            "source": source, "run_at": run_at, "mode": mode,
            "status": status, **counts, "freshness_hours": 0.0
            if endpoints else None, "detail": detail,
        })

    db_file = (config.get("database", {}) or {}).get(
        "file", "history.db")
    conn = histdb.connect(db_file)
    try:
        cache_hours = float(
            (config.get("database", {}) or {}).get("comp_cache_hours", 24))
        cache = conn.execute(
            "SELECT COUNT(*), MAX(scanned_at) FROM comps").fetchone()
        count, newest = cache or (0, None)
        age = None
        if newest:
            try:
                age = max(0.0, (now - datetime.fromisoformat(
                    newest)).total_seconds() / 3600)
            except (TypeError, ValueError):
                age = None
        cache_status = ("empty" if not count else
                        "healthy" if age is not None and age <= cache_hours
                        else "stale")
        rows.append({
            "source": "comp_cache", "run_at": run_at, "mode": mode,
            "status": cache_status, "ok": int(count or 0),
            "failed": 0, "skipped": 0, "freshness_hours": age,
            "detail": f"{int(count or 0):,} rows; freshness limit "
                      f"{cache_hours:g}h",
        })
        histdb.record_source_health(conn, rows)
    finally:
        conn.close()
    return rows
