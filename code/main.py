#!/usr/bin/env python3
"""Collectibles auction/BIN arbitrage scanner.

Usage:
    python main.py                    # full scan (auctions + BINs)
    python main.py --mode bin         # fast BIN sweep (priority queries,
                                      #   cached comps) - cron this often
    python main.py --mode auctions    # auctions only
    python main.py --calibrate        # predicted-vs-actual model report
    python main.py --demo             # synthetic data (no network/keys)
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import time

import yaml

import db as histdb
import paths
from models import Opportunity
from quality import (evidence_rejection, is_tradeable,
                     tradeability_rejection)
from report import write_report
from scrapers import ALL_SCRAPERS
from valuation import ValuationEngine
from report import _category
import valuation.comps as comps_mod
from valuation.comps import (GRADE_RE, grade_conflict, grade_info,
                             language_conflict, subject_missing,
                             title_match_score, variant_conflict)

log = logging.getLogger("scanner")


SECRET_ENV_PATHS = {
    "CARD_SCANNER_EBAY_CLIENT_ID": ("api_keys", "ebay", "client_id"),
    "CARD_SCANNER_EBAY_CLIENT_SECRET": ("api_keys", "ebay", "client_secret"),
    "CARD_SCANNER_PRICECHARTING_TOKEN": ("api_keys", "pricecharting", "token"),
    "CARD_SCANNER_POKEMONTCG_API_KEY": ("api_keys", "pokemontcg", "api_key"),
    "CARD_SCANNER_FANATICS_APP_ID": ("api_keys", "fanatics", "app_id"),
    "CARD_SCANNER_FANATICS_SEARCH_KEY": ("api_keys", "fanatics", "search_key"),
    "CARD_SCANNER_TELEGRAM_BOT_TOKEN":
        ("alerts", "telegram", "bot_token"),
    "CARD_SCANNER_TELEGRAM_CHAT_ID": ("alerts", "telegram", "chat_id"),
}


def _merge_secret_config(config: dict, overlay: dict) -> None:
    """Merge only credential-bearing sections from secrets.yaml."""
    for root in ("api_keys", "alerts"):
        incoming = overlay.get(root)
        if not isinstance(incoming, dict):
            continue
        if root == "alerts":
            incoming = {"telegram": incoming.get("telegram", {})}
        target = config.setdefault(root, {})
        for name, values in incoming.items():
            if not isinstance(values, dict):
                continue
            target.setdefault(name, {}).update(
                {k: v for k, v in values.items() if v not in (None, "")})


def _set_nested(config: dict, path: tuple[str, ...], value: str) -> None:
    node = config
    for part in path[:-1]:
        node = node.setdefault(part, {})
    node[path[-1]] = value


def load_config(path: str) -> dict:
    with open(path) as f:
        config = yaml.safe_load(f) or {}
    # scrapers use this to keep state files (cookie jars) next to config
    # regardless of the cwd cron happens to use
    base = os.path.dirname(os.path.abspath(path))
    config["_config_dir"] = base
    # Keep credentials outside the tracked/main configuration.  An optional
    # secrets.yaml beside config.yaml is loaded first, then environment
    # variables win.  Existing inline keys remain compatible during the
    # transition, so this is safe to roll out before manually moving them.
    secrets_path = os.environ.get(
        "CARD_SCANNER_SECRETS_FILE", os.path.join(base, "secrets.yaml"))
    if os.path.isfile(secrets_path):
        with open(secrets_path) as f:
            _merge_secret_config(config, yaml.safe_load(f) or {})
    for env_name, key_path in SECRET_ENV_PATHS.items():
        value = os.environ.get(env_name)
        if value:
            _set_nested(config, key_path, value)
    # Resolve the database path against config.yaml's folder, not the
    # current directory. A scan launched from anywhere now finds the real
    # history.db instead of silently creating an empty one beside itself.
    dbc = config.setdefault("database", {})
    db_file = dbc.get("file") or paths.DEFAULT_DB
    if not os.path.isabs(db_file):
        db_file = os.path.join(base, db_file)
    parent = os.path.dirname(db_file)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass
    dbc["file"] = db_file
    return config


def _excluded(title: str, keywords: list[str]) -> bool:
    t = title.lower()
    return any(k.lower() in t for k in keywords)


def collection_ok(o, config: dict, drops=None) -> bool:
    """Andrew's collecting standards, applied per listing.

    POKEMON: he only collects 1st Edition and No Rarity. Everything else
    (unlimited, modern) is dropped - EXCEPT where the query already names a
    specific vintage set he collects (Topsun, Carddass, Movie Promo), since
    those are neither 1st Edition nor No Rarity but are exactly the thing
    he asked for.

    VIDEO GAMES: sealed or professionally graded only. Loose cartridges are
    what made every game query value at $9-$70; he wants the collectible
    tier, just with a lower dollar floor than cards.
    """
    def _drop(reason):
        if drops is not None:
            try:
                drops[reason] = drops.get(reason, 0) + 1
            except Exception:
                pass
        return False

    l = o.listing
    if l.grail:
        return True
    flt = config.get("filters", {}) or {}
    cat = _category(l.query)

    if cat == "Pokemon Cards" and flt.get("pokemon_eras_only", True):
        markers = [str(m).lower() for m in
                   (flt.get("pokemon_accepted_eras")
                    or ["1st edition", "first edition", "1st ed", "no rarity"])]
        exempt = [str(m).lower() for m in
                  (flt.get("pokemon_era_exempt_queries")
                   or ["topsun", "carddass", "movie promo", "no rarity"])]
        q = (l.query or "").lower()
        if not any(x in q for x in exempt):
            t = (l.title or "").lower()
            if not any(m in t for m in markers):
                return _drop("Pokemon that is not 1st Edition / No Rarity")

    if cat == "Video Games" and flt.get("video_games_sealed_or_graded", True):
        t = (l.title or "").lower()
        sealed = any(k in t for k in ("sealed", "new in box", "nib",
                                      "shrink wrap", "shrinkwrap"))
        if not (sealed or grade_info(l.title)):
            return _drop("video game that is neither sealed nor graded")
    return True


def run_self_test(config: dict) -> tuple[bool, str]:
    """Run the regression suite before scanning. (passed, one-line summary).

    Run as a SUBPROCESS on purpose: the tests patch logging, build throwaway
    databases and import half the codebase, none of which should touch the
    state of a real scan. Costs about two seconds.

    Fails CLOSED - if the suite cannot be run at all (missing file, import
    error, timeout) that is itself a reason not to trust a live scan, so it
    counts as a failure rather than being waved through.
    """
    cfg = config.get("self_test") or {}
    timeout = cfg.get("timeout_seconds", 180)
    code_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover",
             "-s", code_dir, "-p", "test_*.py"],
            capture_output=True, text=True, timeout=timeout,
            cwd=os.path.dirname(code_dir) or ".")
    except subprocess.TimeoutExpired:
        return False, f"self-test timed out after {timeout}s"
    except (OSError, ValueError) as e:
        return False, f"self-test could not be started ({e})"

    out = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    m = re.search(r"Ran (\d+) tests?", out)
    n = m.group(1) if m else "?"
    if proc.returncode == 0:
        return True, f"self-test: all {n} checks passed"
    problems = [ln.strip() for ln in out.splitlines()
                if ln.startswith(("FAIL:", "ERROR:"))]
    detail = "; ".join(problems[:3]) or (
        out.strip().splitlines()[-1] if out.strip() else "no output")
    return False, f"self-test: {n} checks ran, FAILED - {detail}"


def output_ok(o, *, min_ev: float = 0.0, poke_floor: float = 3.0,
              drops=None) -> bool:
    """Andrew's output rules - the report only shows actionable rows:

      - grails are always kept (they have their own tab; profit is not the
        point of that view)
      - graded POKEMON at or below `poke_floor` in PSA-EQUIVALENT terms are
        dropped. Pokemon only (sports/games/watches untouched); ungraded
        listings are unaffected - a raw card is assumed PSA 5 but can still
        be a gem
      - nothing with negative expected value or negative ROI
      - pure auctions (no buy-it-now) need >= 1 bid: at zero bids the
        "current price" is the seller's opening ask, not a market. Hybrid
        auction+BIN rows are exempt (the BIN is takeable), as is yahoo_jp
        (Buyee does not expose bid counts).

    Module-level ON PURPOSE. This logic used to be a closure inside main(),
    which is how a stray `from report import _category` inside main() made
    `_category` a local of the whole function and killed every run on
    2026-07-25 with a NameError. Nothing here can be shadowed by a local
    import somewhere else in main().
    """
    if drops is None:
        drops = {}

    def _drop(reason):
        try:
            drops[reason] = drops.get(reason, 0) + 1
        except Exception:
            pass
        return False

    try:
        l, v = o.listing, o.valuation
        if l.grail:
            return True
        gi = grade_info(l.title)
        if (gi and float(gi[2]) <= poke_floor
                and _category(l.query) == "Pokemon Cards"):
            return _drop("graded Pokemon at or below PSA %g" % poke_floor)
        if v.expected_value < min_ev:
            return _drop("expected value < $%g" % min_ev)
        if v.roi < 0:
            return _drop("negative ROI")
        # A zero-bid auction has no market yet. The hybrid exemption only
        # holds when we actually KNOW the buy-it-now price - otherwise the
        # row would be priced off the seller's opening ask.
        takeable_bin = l.has_buy_now and getattr(l, "buy_now_price", 0) > 0
        if (l.listing_type == "auction" and l.bid_count < 1
                and not takeable_bin and l.site != "yahoo_jp"):
            return _drop("zero-bid auction with no takeable buy-it-now")
        return True
    except Exception:
        # a bad row must never kill the whole report - log it loudly (with
        # the listing so it's findable) and keep the row visible
        log.exception("output_ok crashed on listing %r (site=%s, query=%r)"
                      " - keeping row",
                      getattr(o.listing, "title", "?"),
                      getattr(o.listing, "site", "?"),
                      getattr(o.listing, "query", "?"))
        return True


def persist_trusted_evidence(conn, o: Opportunity, config: dict,
                             fair_recorded: bool = False
                             ) -> tuple[bool, str | None]:
    """Persist only evidence that is safe for trends and model training.

    Returns ``(fair_recorded, rejection_reason)`` so run_live can record one
    fair-history point per query and summarize everything the gate rejected.
    """
    algo = config.get("algorithm", {}) or {}
    min_fair = algo.get(
        "learner_min_fair", algo.get("min_observation_fair", 50.0))
    min_comps = algo.get("learner_min_comps", 3)
    reason = evidence_rejection(
        o, collection_passed=collection_ok(o, config),
        min_fair=min_fair, min_comps=min_comps)
    if reason:
        return fair_recorded, reason

    l, v = o.listing, o.valuation
    if not fair_recorded:
        histdb.record_fair(conn, l.query, v.fair_value, v.n_comps)
        fair_recorded = True
    v.trend_30d = histdb.trend_30d(conn, l.query, v.fair_value)
    # record_observation itself ignores BINs and listings without a stable id.
    histdb.record_observation(conn, l, v)
    return fair_recorded, None


def plan_targeted_comp_queries(listings: list, engine: ValuationEngine,
                               limit: int) -> list[str]:
    """Prioritized, deduplicated exact comp searches for numbered cards."""
    if limit <= 0:
        return []
    candidates: dict[str, tuple] = {}
    for listing in listings:
        if (_category(listing.query)
                not in {"Pokemon Cards", "Sports Cards"}):
            continue
        query = engine.targeted_comp_query(listing)
        if not query:
            continue
        timing = (listing.age_hours if listing.listing_type == "fixed"
                  else listing.hours_remaining)
        rank = (0 if listing.priority else 1,
                timing if timing is not None else 1e9,
                listing.total_cost_now)
        if query not in candidates or rank < candidates[query]:
            candidates[query] = rank
    return [
        query for query, _ in
        sorted(candidates.items(), key=lambda item: item[1])[:limit]
    ]


def run_live(config: dict, engine: ValuationEngine, mode: str,
             diagnostics: dict | None = None) -> list[Opportunity]:
    sites = config.get("sites", ["ebay"])
    max_results = config.get("scraping", {}).get("max_results_per_query", 40)
    flt = config.get("filters", {})
    price_max = flt.get("max_price") or float("inf")
    exclude = flt.get("exclude_keywords") or []
    dbc = config.get("database", {})
    conn = histdb.connect(dbc.get("file", "history.db"))
    cache_hours = dbc.get("comp_cache_hours", 24)

    scrapers = {s: ALL_SCRAPERS[s](config) for s in sites if s in ALL_SCRAPERS}
    ebay = scrapers.get("ebay")

    scfg = config.get("scraping", {})
    p130 = None
    if scfg.get("use_130point", True):
        from scrapers.point130 import Point130Scraper
        p130 = Point130Scraper(config)
    # eBay sold-page fetching can be paused (bot-block cooldown) with an
    # automatic resume time so nobody has to remember to flip it back
    use_html = scfg.get("use_html_comps", True)
    resume = scfg.get("html_comps_resume")
    if not use_html and resume:
        from datetime import datetime
        if datetime.now() >= datetime.fromisoformat(str(resume)):
            use_html = True
            log.info("eBay sold-page fetching auto-resumed (cooldown over)")

    entries = config.get("watchlist", [])
    if mode == "bin" and config.get("bin", {}).get("priority_only", True):
        entries = [e for e in entries
                   if e.get("priority") or GRADE_RE.search(e["query"])]
        log.info("BIN sweep: %d priority queries", len(entries))

    # grail hunt: full scans also search every personal-collection grail as
    # its own soft-valued query (discovery -> quarantined from alerts/ML),
    # and every listing from every query gets checked against the list
    import grails as grails_mod
    grail_list = grails_mod.load_grails(config)
    # grails must be substantial: below this price a "match" on a generic
    # grail (Batman, Grant Hill...) is almost certainly not the real thing
    grail_min = config.get("grail_min_price", 3000)
    if mode == "all" and grail_list:
        have = {e["query"].lower() for e in entries}
        entries = entries + [{"query": g.name, "discovery": True}
                             for g in grail_list
                             if g.name.lower() not in have]
        log.info("grail hunt: %d grail queries added", len(grail_list))

    # ---- phase A (main thread): read comp caches; plan the network work
    plans = []
    for entry in entries:
        cached = histdb.cached_comps(conn, entry["query"], cache_hours) or []
        plans.append((entry, cached))

    # prefetch the OAuth token once so parallel workers don't race for it
    if ebay:
        ebay._get_token()

    # ---- phase B (parallel): all network fetching. Queries run in a small
    # thread pool; per-site politeness is preserved because each scraper's
    # HTML lane is a lock (one request at a time, same delays as before).
    # No DB access happens in workers - SQLite connections aren't shared
    # across threads.
    mis_cfg = config.get("misspell", {})
    intl_pri_only = config.get("scraping", {}).get(
        "international_priority_only", True)

    def fetch(plan):
        entry, cached = plan
        query = entry["query"]
        discovery = bool(entry.get("discovery"))
        priority = (not discovery) and (bool(entry.get("priority"))
                                        or bool(GRADE_RE.search(query)))
        log.info("query: %s", query)
        try:
            # comps: fresh cache -> 130point -> eBay sold pages
            fetched = []
            if not cached:
                if p130:
                    fetched = p130.search_sold(query)
                if not fetched and ebay and use_html:
                    fetched = ebay.search_sold(query)

            # speed: international marketplaces + Yahoo JP only for priority
            # queries (that's where the cross-market edge is), unless disabled
            intl = priority or not intl_pri_only

            listings = []
            if mode in ("all", "auctions"):
                for name, s in scrapers.items():
                    if name == "yahoo_jp":
                        if not intl:
                            continue
                        found = s.search_auctions(query, max_results,
                                                  query_ja=entry.get("query_ja"))
                    elif name == "ebay":
                        found = s.search_auctions(query, max_results, intl=intl)
                    else:
                        found = s.search_auctions(query, max_results)
                    log.info("  %s: %d auctions (%s)", name, len(found), query)
                    listings += found
            if mode in ("all", "bin") and ebay:
                found = ebay.search_fixed(query, max_results, intl=intl)
                log.info("  ebay: %d fixed-price (%s)", len(found), query)
                listings += found

            # misspelling hunter: priority queries only, full scans only (to
            # stay inside API quota) - typo'd listings get few bidders
            if (mode == "all" and priority and ebay
                    and mis_cfg.get("enabled", True)):
                from misspell import variant_queries
                n_mis = 0
                for vq in variant_queries(query, mis_cfg.get("max_variants", 4)):
                    for l in (ebay.search_auctions(vq, 20, intl=False)
                              + ebay.search_fixed(vq, 20, intl=False)):
                        l.misspell_from = vq
                        listings.append(l)
                        n_mis += 1
                if n_mis:
                    log.info("  misspell variants: %d listings (%s)",
                             n_mis, query)
        except Exception:
            # one bad query must not kill an unattended cron run
            log.exception("query %r failed - skipping", query)
            return entry, cached, [], []
        return entry, cached, fetched, listings

    t_fetch = time.monotonic()
    workers = max(1, int(scfg.get("parallel_queries", 6)))
    if workers > 1 and len(plans) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(fetch, plans))
    else:
        results = [fetch(p) for p in plans]
    t_fetch = time.monotonic() - t_fetch

    # ---- phase C (main thread): DB writes + listing relevance
    # Rejections from the centralized evidence gate.  The report's browsing
    # tabs may still show these rows, but trends and learning never see them.
    import collections
    evidence_skips: collections.Counter = collections.Counter()
    relevance_skips: collections.Counter = collections.Counter()
    raw_by_site: collections.Counter = collections.Counter(
        listing.site
        for _, _, _, listings in results
        for listing in listings)
    raw_count = sum(raw_by_site.values())
    n_comp_junk = [0]        # list so the loop below can accumulate into it
    t_val = time.monotonic()
    prepared = []
    for entry, cached, fetched, listings in results:
        query = entry["query"]
        cap = entry.get("max_buy_price")
        discovery = bool(entry.get("discovery"))
        priority = (not discovery) and (bool(entry.get("priority"))
                                        or bool(GRADE_RE.search(query)))

        comps = cached
        if not comps:
            if fetched:
                comps = fetched
                histdb.save_comps(conn, query, comps)
                matched = histdb.match_closed(conn, comps)
                if matched:
                    log.info("  calibration: matched %d closed auctions (%s)",
                             matched, query)
            else:
                comps = histdb.cached_comps(conn, query, cache_hours,
                                            allow_stale=True) or []
                if comps:
                    log.info("  using stale comp cache for %s "
                             "(fresh fetch blocked)", query)
        # Screen the COMPS with the same keyword blacklist the listings get.
        # It was only ever applied to listings, so 415 of 8,114 cached comps
        # were "reprint" / "you pick" lots quietly setting fair values - the
        # Babe Ruth 1933 pool had a median of $6 because of them.
        if exclude and comps:
            n_raw = len(comps)
            comps = [c for c in comps if not _excluded(c.title, exclude)]
            if n_raw != len(comps):
                n_comp_junk[0] += n_raw - len(comps)
        log.info("  %d sold comps (%s)", len(comps), query)

        min_match = flt.get("min_listing_match", 0.6)
        seen_ids: set[str] = set()
        relevant: list[tuple[str, object]] = []
        for listing in listings:
            # dedupe: the same item shows up once per marketplace searched
            m = histdb.ITEM_ID_RE.search(listing.url or "")
            key = m.group(1) if m else (listing.listing_id or listing.url)
            if key in seen_ids:
                relevance_skips["duplicate within query"] += 1
                continue
            seen_ids.add(key)
            if _excluded(listing.title, exclude):
                relevance_skips["excluded keyword"] += 1
                continue
            # relevance guard: wrong grade or barely-matching title = out
            # (Japanese titles can't fuzzy-match English; they're exempt
            # and handled by the engine's JP confidence rule instead)
            if grade_conflict(query, listing.title):
                relevance_skips["grade conflict"] += 1
                continue
            # foreign-language versions price differently - not our market
            if listing.site != "yahoo_jp" and language_conflict(query, listing.title):
                relevance_skips["language conflict"] += 1
                continue
            # holo/non-holo, 1st ed/unlimited/shadowless = different cards
            if variant_conflict(query, listing.title):
                relevance_skips["variant conflict"] += 1
                continue
            if (listing.site != "yahoo_jp" and not discovery
                    and not listing.misspell_from):  # typo'd titles can't match
                # wrong-subject guard: a Magneton is not a Gengar no matter
                # how well the set/grade context matches
                if subject_missing(query, listing.title):
                    relevance_skips["wrong subject"] += 1
                    continue
                if title_match_score(query, listing.title) < min_match:
                    relevance_skips["title match below threshold"] += 1
                    continue
            if cap and listing.total_cost_now > cap:
                relevance_skips["over query price cap"] += 1
                continue
            if listing.total_cost_now > price_max:
                relevance_skips["over global price cap"] += 1
                continue
            listing.priority = priority
            listing.discovery = discovery
            listing.category = _category(query)
            listing.resale_channel = str(
                entry.get("resale_channel")
                or config.get("algorithm", {}).get(
                    "default_resale_channel", "auto"))
            # grail tagging: any listing from any query can be a grail -
            # but only at grail money ($3k+ default; cheap "matches" on
            # generic names are noise, not grails)
            gm = grails_mod.match(grail_list, listing.title)
            if (gm and listing.total_cost_now >= grail_min
                    and not (gm.max_price
                             and listing.total_cost_now > gm.max_price)):
                listing.grail = gm.name
                listing.grail_score = gm.score
            relevant.append((key, listing))

        # live fixed-price asks for this query: last-resort valuation
        # source when sold comps and guide prices are both unavailable
        ask_pool = [(k, l.total_cost_now) for k, l in relevant
                    if l.listing_type == "fixed" and l.total_cost_now > 0]
        prepared.append((query, comps, relevant, ask_pool))

    # ---- phase D: exact comp pools for numbered cards -------------------
    # Broad live searches are good at FINDING cards. They are not allowed to
    # PRICE #101, #288 and #195 from one shared median. Once listing titles
    # reveal card number + grade, fetch/cache a separate sold pool for each
    # identity. Limits prevent a broad scan from multiplying into hundreds
    # of requests; priority and ending/fresh-soon listings go first.
    target_default = 6 if mode == "bin" else 20
    target_limit = int(scfg.get(
        "targeted_comp_queries_per_bin_run" if mode == "bin"
        else "targeted_comp_queries_per_run", target_default))
    target_max = int(scfg.get("targeted_comp_max_results", 60))
    all_relevant = [listing for _, _, rows, _ in prepared
                    for _, listing in rows]
    target_queries = plan_targeted_comp_queries(
        all_relevant, engine, target_limit)
    targeted_pools: dict[str, list] = {}
    target_missing = []
    for target_query in target_queries:
        pool = histdb.cached_comps(
            conn, target_query, cache_hours, min_count=1) or []
        if pool:
            targeted_pools[target_query] = pool
        else:
            target_missing.append(target_query)

    def fetch_targeted(target_query: str):
        try:
            pool = []
            if p130 and not p130.tripped:
                pool = p130.search_sold(target_query, target_max)
            if not pool and ebay and use_html and not ebay.tripped:
                pool = ebay.search_sold(target_query, target_max)
            return target_query, pool
        except Exception:
            # One malformed title/query or scraper parser change must not
            # abort every valuation after the main discovery work succeeded.
            log.exception("targeted comp query %r failed - using broad "
                          "quarantined fallback", target_query)
            return target_query, []

    target_started = time.monotonic()
    target_results = []
    if target_missing:
        if workers > 1 and len(target_missing) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(workers, 4)) as ex:
                target_results = list(ex.map(fetch_targeted, target_missing))
        else:
            target_results = [fetch_targeted(q) for q in target_missing]
    for target_query, pool in target_results:
        if pool:
            histdb.save_comps(conn, target_query, pool)
            histdb.match_closed(conn, pool)
        else:
            pool = histdb.cached_comps(
                conn, target_query, cache_hours, min_count=1,
                allow_stale=True) or []
        targeted_pools[target_query] = pool
    t_fetch += time.monotonic() - target_started

    if exclude:
        for target_query, pool in list(targeted_pools.items()):
            clean = [c for c in pool if not _excluded(c.title, exclude)]
            n_comp_junk[0] += len(pool) - len(clean)
            targeted_pools[target_query] = clean
    if target_queries:
        usable = sum(
            len(pool) >= engine.min_specific_comps
            for pool in targeted_pools.values())
        log.info("targeted comps: %d exact card queries planned (%d cache "
                 "hits, %d cache misses, %d with >=%d rows)",
                 len(target_queries), len(target_queries) - len(target_missing),
                 len(target_missing), usable, engine.min_specific_comps)

    # ---- phase E: valuation + trusted persistence -----------------------
    opps: list[Opportunity] = []
    for query, comps, relevant, ask_pool in prepared:
        fair_recorded = False
        for key, listing in relevant:
            asks = [c for k, c in ask_pool if k != key]
            target_query = engine.targeted_comp_query(listing)
            specific = (targeted_pools.get(target_query)
                        if target_query in targeted_pools else None)
            opp = engine.evaluate(
                listing, comps, asks, specific_comps=specific)
            v = opp.valuation
            fair_recorded, rejected = persist_trusted_evidence(
                conn, opp, config, fair_recorded)
            if rejected:
                evidence_skips[rejected] += 1
            opps.append(opp)

    # BIN sweeps run all day: use each one to quietly refresh a few stale
    # background comp caches so the big daily scan finds everything warm
    warm_src = (p130 if (p130 and not p130.tripped)
                else ebay if (ebay and use_html and not ebay.tripped) else None)
    if mode == "bin" and warm_src:
        warm_n = config.get("scraping", {}).get("comps_warm_per_sweep", 3)
        active = {e["query"] for e in entries}
        warmed = 0
        for e in config.get("watchlist", []):
            if warmed >= warm_n:
                break
            # the source can trip PART WAY through warming (this loop is the
            # one piece of work unique to BIN sweeps) - stop asking the
            # moment it does, instead of walking the rest of the watchlist
            if warm_src.tripped:
                log.info("comp warming stopped early: %s is backing off",
                         warm_src.site)
                break
            q = e["query"]
            if q in active or histdb.cached_comps(conn, q, cache_hours):
                continue
            c = warm_src.search_sold(q)
            if c:
                histdb.save_comps(conn, q, c)
                histdb.match_closed(conn, c)
                warmed += 1
        if warmed:
            log.info("warmed comp caches for %d background queries", warmed)

    conn.close()
    if n_comp_junk[0]:
        log.info("comp screen: ignored %d sold comp(s) matching an excluded "
                 "keyword (reprints, 'you pick' lots) - they were setting "
                 "fair values", n_comp_junk[0])
    if evidence_skips:
        log.info("evidence gate: %d valuation(s) kept out of learner/history "
                 "(%s)", sum(evidence_skips.values()),
                 "; ".join(f"{reason} x{n}"
                           for reason, n in evidence_skips.most_common()))
    relevant_count = sum(len(rows) for _, _, rows, _ in prepared)
    if diagnostics is not None:
        diagnostics.update({
            "queries": len(plans),
            "raw_listings": raw_count,
            "raw_by_site": dict(raw_by_site),
            "relevance_removed": sum(relevance_skips.values()),
            "relevance_reasons": dict(relevance_skips),
            "relevant_listings": relevant_count,
            "valued_listings": len(opps),
            "evidence_quarantined": sum(evidence_skips.values()),
            "evidence_reasons": dict(evidence_skips),
            "comp_junk_removed": n_comp_junk[0],
        })
    log.info(
        "scan funnel: %d raw listing hits -> %d relevant -> %d valued "
        "(sources: %s; relevance drops: %s)",
        raw_count, relevant_count, len(opps),
        ", ".join(f"{site}={count}"
                  for site, count in sorted(raw_by_site.items())) or "none",
        "; ".join(f"{reason} x{count}"
                  for reason, count in relevance_skips.most_common())
        or "none")
    if comps_mod.UNPARSEABLE_GRADES:
        log.info("grade parser: ignored %d impossible grade token(s): %s",
                 sum(comps_mod.UNPARSEABLE_GRADES.values()),
                 ", ".join(f"{k} x{v}" for k, v in
                           comps_mod.UNPARSEABLE_GRADES.most_common(8)))
    # where did the minutes go? (network fetch vs valuation+DB) - the
    # answer to "why was this run slow" should be one grep away
    log.info("timing: fetch %.0fs, valuation+db %.0fs (%d queries, "
             "%d workers)", t_fetch, time.monotonic() - t_val,
             len(plans), workers)
    return opps


def run_demo(config: dict, engine: ValuationEngine) -> list[Opportunity]:
    import demo_data
    engine.guide.guide_value = demo_data.demo_guide_value  # stub guide API
    opps = []
    for query in demo_data.MARKET:
        comps = demo_data.demo_comps(query)
        for listing in demo_data.demo_listings(query):
            opps.append(engine.evaluate(listing, comps))
    return opps


def classify_report_rows(opps: list[Opportunity], config: dict
                         ) -> tuple[list[Opportunity], list[dict], dict]:
    """Apply report filters once, preserving every rejected valued row.

    Decision tabs remain strict.  The returned research records explain why
    a valued listing disappeared before the workbook, and also identify rows
    that remain browsable but are quarantined from Action/Today/alerts.
    """
    import collections

    min_value = config.get("filters", {}).get("min_value", 0)
    by_cat = config.get("filters", {}).get("min_value_by_category") or {}
    max_roi = config.get("filters", {}).get("max_roi", 2.0)
    min_ev = config.get("output", {}).get("min_expected_value", 0)
    poke_floor = config.get("filters", {}).get("pokemon_grade_floor", 3.0)
    max_rows = config.get("output", {}).get("max_rows", 1000)

    counts: collections.Counter = collections.Counter()
    reasons: collections.Counter = collections.Counter()
    research: list[dict] = []
    kept: list[Opportunity] = []

    def reject(o: Opportunity, stage: str, reason: str) -> None:
        counts[stage] += 1
        reasons[reason] += 1
        research.append({
            "stage": stage,
            "reason": reason,
            "opportunity": o,
        })

    for o in opps:
        floor = max(
            by_cat.get(_category(o.listing.query), min_value), 0.01)
        if o.valuation.fair_value < floor:
            reject(
                o, "Fair-value floor",
                "fair value $%s below %s floor $%s" % (
                    f"{o.valuation.fair_value:,.0f}",
                    _category(o.listing.query),
                    f"{floor:,.0f}"))
            continue
        if o.valuation.roi > max_roi:
            reject(
                o, "ROI sanity ceiling",
                "ROI %.0f%% above %.0f%% ceiling"
                % (o.valuation.roi * 100, max_roi * 100))
            continue

        collection_drops: collections.Counter = collections.Counter()
        if not collection_ok(o, config, drops=collection_drops):
            reason = next(iter(collection_drops), "outside collection standards")
            reject(o, "Collection standards", reason)
            continue

        output_drops: collections.Counter = collections.Counter()
        if not output_ok(
                o, min_ev=min_ev, poke_floor=poke_floor,
                drops=output_drops):
            reason = next(iter(output_drops), "output rule")
            reject(o, "Output economics", reason)
            continue
        kept.append(o)

    if len(kept) > max_rows:
        grails = [o for o in kept if o.listing.grail]
        ranked = sorted(
            (o for o in kept if not o.listing.grail),
            key=lambda o: o.valuation.opportunity_score,
            reverse=True)
        keep_ids = {
            id(o) for o in
            grails + ranked[:max(0, max_rows - len(grails))]
        }
        trimmed = [o for o in kept if id(o) not in keep_ids]
        kept = [o for o in kept if id(o) in keep_ids]
        for o in trimmed:
            reject(o, "Report row cap", "below configured report row cap")

    # A row can remain visible in a category or Grails tab but still be
    # barred from Action/Today/alerts.  Put a copy in Research/Filtered so
    # an empty Action tab always has a row-by-row explanation.
    for o in kept:
        reason = tradeability_rejection(o)
        if reason:
            research.append({
                "stage": "Decision-only quarantine",
                "reason": reason,
                "opportunity": o,
            })
            counts["Decision-only quarantine"] += 1
            reasons[reason] += 1

    return kept, research, {
        "stage_counts": dict(counts),
        "reason_counts": dict(reasons),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Collectibles auction EV scanner")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--mode", choices=["all", "auctions", "bin"], default="all")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--calibrate", action="store_true",
                    help="print predicted-vs-actual calibration report")
    ap.add_argument("--skip-self-test", action="store_true",
                    help="scan even if the regression suite fails "
                         "(override - the scan is normally blocked)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    # persistent log so cron runs leave evidence (scan.log next to config,
    # ~2MB x 3 rotations). Every run logs start/end, comps counts, alert
    # decisions and delivery results - "did alerts fire today?" is now
    # answerable from the file instead of guesswork.
    try:
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(
            paths.file_in(paths.base_dir(config_path=args.config),
                          paths.LOGS, "scan.log"),
            maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        fh.setLevel(logging.INFO)
        logging.getLogger().addHandler(fh)
    except OSError:
        pass
    log.info("=== run start: mode=%s demo=%s ===", args.mode, args.demo)

    config = load_config(args.config)

    # --- self-test gate --------------------------------------------------
    # Run the regression suite BEFORE touching anything. If a check fails,
    # the valuation logic can no longer be trusted, so the scan is stopped
    # before it takes the lock, hits eBay, writes to the database or
    # produces a report anyone might act on. --demo and --calibrate are
    # exempt: they are how you diagnose a failure.
    if not (args.demo or args.calibrate or args.skip_self_test):
        if (config.get("self_test") or {}).get("before_every_scan", True):
            passed, summary = run_self_test(config)
            if not passed:
                log.error("TEST RUN FAILED - scan aborted. %s", summary)
                log.error("Nothing was scanned, nothing was written and no "
                          "report was produced. Double-click 'Run Tests."
                          "command' to see which check failed. To scan "
                          "anyway, add --skip-self-test.")
                return 2
            log.info("%s", summary)

    # --- single-run lock -------------------------------------------------
    # Overlapping scans (manual run + cron sweep) defeat the per-site
    # politeness delays - two processes hitting eBay/130point at once look
    # like a bot swarm and invite blocks - and contend on SQLite writes.
    # BIN sweeps just skip (the next one is <=30 min away). Full scans
    # PREEMPT whatever holds the lock: cron sweeps now take 15-20+ min
    # (bot-challenge cooldowns), so the lock is held almost continuously
    # and the old "wait up to 15 min" made every manual run look broken.
    # Losing a sweep is harmless - the next one is <=30 min away.
    lock_handle = None          # held (not closed) for the whole run
    if not args.demo and not args.calibrate:
        import fcntl
        import signal
        lock_path = paths.file_in(paths.base_dir(config_path=args.config),
                                  paths.STATE, ".scan.lock")
        # "a+" not "w": must NOT truncate - the holder's "<pid> <mode>
        # <started>" line is what makes the messages below informative.
        lock_handle = open(lock_path, "a+")

        def _try_lock():
            try:
                fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                return False

        if not _try_lock():
            try:
                lock_handle.seek(0)
                pid_s, h_mode, h_start = lock_handle.read().split()[:3]
                holder_pid = int(pid_s)
                holder = "pid %s, mode=%s, started %s" % (pid_s, h_mode,
                                                          h_start)
            except (ValueError, IndexError, OSError):
                holder_pid, holder = None, "unknown holder"
            if args.mode == "bin":
                log.info("another scan is already running (%s) - skipping "
                         "this sweep (next one runs in 30 min)", holder)
                return 0
            log.info("another scan is running (%s) - stopping it so this "
                     "run can start now", holder)
            acquired = False
            if holder_pid:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.kill(holder_pid, sig)
                    except (ProcessLookupError, PermissionError):
                        pass
                    deadline = time.time() + 20
                    while time.time() < deadline:
                        if _try_lock():
                            acquired = True
                            break
                        time.sleep(1)
                    if acquired:
                        break
            if not acquired and not _try_lock():
                log.warning("could not take over the scan lock (holder: %s)"
                            " - giving up on this run", holder)
                return 1
        # record who holds the lock so contenders can log something useful
        try:
            lock_handle.seek(0)
            lock_handle.truncate()
            lock_handle.write("%d %s %s\n" % (
                os.getpid(), args.mode,
                time.strftime("%Y-%m-%dT%H:%M:%S")))
            lock_handle.flush()
        except OSError:
            pass

    if args.calibrate:
        conn = histdb.connect(config.get("database", {}).get("file", "history.db"))
        print(histdb.calibration_report(conn))
        return 0

    engine = ValuationEngine(config)
    scan_diagnostics: dict = {}
    opps = (run_demo(config, engine) if args.demo
            else run_live(
                config, engine, args.mode, diagnostics=scan_diagnostics))
    if args.demo:
        scan_diagnostics.update({
            "queries": len(getattr(__import__("demo_data"), "MARKET", {})),
            "raw_listings": len(opps),
            "raw_by_site": {"demo": len(opps)},
            "relevance_removed": 0,
            "relevance_reasons": {},
            "relevant_listings": len(opps),
            "valued_listings": len(opps),
            "evidence_quarantined": 0,
            "evidence_reasons": {},
            "comp_junk_removed": 0,
        })
    health_rows = []
    if not args.demo:
        try:
            from source_health import capture as capture_source_health
            health_rows = capture_source_health(config, args.mode)
            unhealthy = [r["source"] for r in health_rows
                         if r["status"] in {
                             "degraded", "cooling", "failing",
                             "stale", "empty"}]
            if unhealthy:
                log.warning("source health needs attention: %s",
                            ", ".join(unhealthy))
        except Exception:
            log.exception("source-health snapshot failed - continuing")

    valued_opps = list(opps)
    kept, research_rows, report_diagnostics = classify_report_rows(
        valued_opps, config)
    stage_counts = report_diagnostics["stage_counts"]

    raw = int(scan_diagnostics.get("raw_listings", len(valued_opps)))
    relevant = int(scan_diagnostics.get(
        "relevant_listings", len(valued_opps)))
    valued = len(valued_opps)
    remaining = valued
    waterfall_rows = [{
        "stage": "Raw marketplace hits",
        "starting": raw,
        "removed": 0,
        "remaining": raw,
        "detail": ", ".join(
            f"{site}={count}" for site, count in sorted(
                (scan_diagnostics.get("raw_by_site") or {}).items()))
            or "no source returned listings",
    }, {
        "stage": "Relevance and identity",
        "starting": raw,
        "removed": max(0, raw - relevant),
        "remaining": relevant,
        "detail": "; ".join(
            f"{reason} x{count}" for reason, count in sorted(
                (scan_diagnostics.get("relevance_reasons") or {}).items(),
                key=lambda item: -item[1])) or "none",
    }, {
        "stage": "Valuation completed",
        "starting": relevant,
        "removed": max(0, relevant - valued),
        "remaining": valued,
        "detail": "sold comps / guides / cost and exit economics evaluated",
    }]
    for stage in (
            "Fair-value floor", "ROI sanity ceiling",
            "Collection standards", "Output economics", "Report row cap"):
        removed = int(stage_counts.get(stage, 0))
        waterfall_rows.append({
            "stage": stage,
            "starting": remaining,
            "removed": removed,
            "remaining": remaining - removed,
            "detail": "; ".join(
                f"{reason} x{count}"
                for reason, count in
                report_diagnostics["reason_counts"].items()
                if any(row["stage"] == stage and row["reason"] == reason
                       for row in research_rows)) or "none",
        })
        remaining -= removed
    waterfall_rows.extend([{
        "stage": "Final workbook rows",
        "starting": remaining,
        "removed": 0,
        "remaining": len(kept),
        "detail": "kept in category/Grails tabs",
    }, {
        "stage": "Evidence-only quarantine",
        "starting": valued,
        "removed": int(scan_diagnostics.get(
            "evidence_quarantined", 0)),
        "remaining": valued,
        "detail": "does not remove workbook rows; blocks learning/history: "
                  + ("; ".join(
                      f"{reason} x{count}" for reason, count in sorted(
                          (scan_diagnostics.get("evidence_reasons")
                           or {}).items(),
                          key=lambda item: -item[1])) or "none"),
    }, {
        "stage": "Decision-only quarantine",
        "starting": len(kept),
        "removed": int(stage_counts.get(
            "Decision-only quarantine", 0)),
        "remaining": len(kept) - int(stage_counts.get(
            "Decision-only quarantine", 0)),
        "detail": "still browsable; blocked from Action/Today/alerts",
    }])

    log.info(
        "filter waterfall: raw %d -> relevant %d -> valued %d -> "
        "fair floor %d -> ROI %d -> collection %d -> output %d -> "
        "row cap %d -> final %d",
        raw, relevant, valued,
        stage_counts.get("Fair-value floor", 0),
        stage_counts.get("ROI sanity ceiling", 0),
        stage_counts.get("Collection standards", 0),
        stage_counts.get("Output economics", 0),
        stage_counts.get("Report row cap", 0), len(kept))
    if report_diagnostics["reason_counts"]:
        log.info(
            "filter reasons: %s",
            "; ".join(
                f"{reason} x{count}" for reason, count in sorted(
                    report_diagnostics["reason_counts"].items(),
                    key=lambda item: -item[1])))
    log.info(
        "report: %d kept row(s), %d decision-tradeable, "
        "%d Research/Filtered explanation row(s)",
        len(kept),
        len(kept) - int(stage_counts.get(
            "Decision-only quarantine", 0)),
        len(research_rows))
    if not kept:
        if valued_opps:
            log.info("scanned %d valued listings; none passed the strict "
                     "decision/output rules - writing a diagnostic workbook",
                     len(valued_opps))
        else:
            log.error("nothing scanned at all - writing source-health "
                      "diagnostics; check network/API keys/scan.log")

    out = args.output or config.get("output", {}).get("file", "opportunities.xlsx")
    # portfolio: mark positions to market with this run's fair values,
    # falling back to the latest recorded history for unscanned queries
    portfolio_rows = None
    if not args.demo:
        try:
            import portfolio as pf
            pf_dir = paths.folder(paths.base_dir(config), paths.PORTFOLIO)
            pf.ensure_template(pf_dir)
            conn = histdb.connect(
                config.get("database", {}).get("file", paths.DEFAULT_DB))
            fairs = pf.latest_fairs(conn)
            conn.close()
            for o in kept:
                v = o.valuation
                if is_tradeable(o):
                    fairs[o.listing.query.lower()] = v.fair_value
            portfolio_rows = pf.build_rows(config, fairs, pf_dir)
        except Exception:
            log.exception("portfolio marking failed - continuing")
    write_report(
        kept, out, portfolio=portfolio_rows, config=config,
        source_health=health_rows, research=research_rows,
        filter_waterfall=waterfall_rows)
    opps = kept

    if not args.demo and opps:
        from alerts import send_alerts
        db_file = config.get("database", {}).get("file", "history.db")
        n_alerts = send_alerts(opps, config, db_file)
        if n_alerts:
            log.info("sent alert for %d new hot listing(s)", n_alerts)
        # full-scan digest: top opportunities + top grails to Telegram
        if args.mode in ("all", "auctions"):
            try:
                from digest import send_digest
                # NOTE: _category is imported at module top. Do NOT re-import
                # it here - a function-local `from report import _category`
                # makes _category local to ALL of main(), so output_ok's
                # reference crashed with NameError (killed every run 07/25
                # 09:30-13:30 right after the too-good-to-be-true line,
                # before the report was written).
                watch_qs = {o.listing.query for o in opps
                            if _category(o.listing.query) == "Watches"}
                n_msg = send_digest(opps, config, watch_qs)
                if n_msg:
                    log.info("digest: sent %d telegram message(s)", n_msg)
            except Exception:
                log.exception("digest failed - continuing")
        # settle recently-ended auctions -> exact calibration ground truth
        # (every run, so 30-min sweeps keep the closed table current)
        try:
            from closer import settle_closes
            t_close = time.monotonic()
            log.info(settle_closes(config))
            log.info("timing: closer %.0fs", time.monotonic() - t_close)
        except Exception:
            log.exception("closer failed - continuing")
        if args.mode in ("all", "auctions"):
            import learner
            log.info(learner.fit(
                db_file, directory=paths.folder(paths.base_dir(config),
                                                paths.MODEL),
                config=config))
    log.info("wrote %d kept opportunities and %d research explanations -> %s",
             len(opps), len(research_rows), out)
    if opps:
        best = max(opps, key=lambda o: o.valuation.opportunity_score)
        log.info("top: %s | edge $%.0f | score %.1f%%",
                 best.listing.title[:60], best.valuation.edge_now,
                 best.valuation.opportunity_score * 100)
    return 1 if not valued_opps else 0


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _run_footer(started: float, mode: str, rc) -> None:
    """Last thing every run prints, whatever happened to it.

    Two questions this answers without digging: how long did that take,
    and did we hammer anything that was already failing? The API line
    shows ok/failed/skipped per endpoint - 'skipped' means the breaker was
    open and we deliberately did not call it. Identical in every mode: the
    full scan and the BIN sweep are the same program.
    """
    from scrapers.base import api_summary
    try:
        log.info("api calls: %s", api_summary())
    except Exception:
        pass
    log.info("=== run finished in %s (mode=%s, exit=%s) ===",
             _format_duration(time.monotonic() - started), mode, rc)


if __name__ == "__main__":
    _started = time.monotonic()
    _mode = "all"
    for _i, _a in enumerate(sys.argv):
        if _a == "--mode" and _i + 1 < len(sys.argv):
            _mode = sys.argv[_i + 1]
        elif _a.startswith("--mode="):
            _mode = _a.split("=", 1)[1]
        elif _a == "--demo":
            _mode = "demo"
    _rc = 1
    try:
        _rc = main()
    except SystemExit as _e:          # argparse --help / bad arguments
        _rc = _e.code if isinstance(_e.code, int) else 0
    except Exception:
        # tracebacks used to go only to the Terminal window (lost when it
        # closes) - runs died with no trace in scan.log. Log, don't re-raise:
        # the footer below must still run.
        log.exception("scan crashed with an unhandled error")
        _rc = 1
    finally:
        _run_footer(_started, _mode, _rc)
    sys.exit(_rc)
