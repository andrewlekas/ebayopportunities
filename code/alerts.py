"""Snipe alerts: Telegram message (and/or macOS notification) when a scan
finds a hot opportunity.

Fires for listings whose opportunity score clears alerts.min_score
(priority queries only, by default). Each listing alerts once ever -
alerted item keys are stored in the history DB so 30-minute cron sweeps
don't re-notify the same deal.

config.yaml:
    alerts:
      enabled: true
      min_score: 0.12        # opportunity score threshold (12%)
      min_edge_now: 100      # AND at least this many $ of edge at current price
      priority_only: true
      macos_notification: true
      sound: true
      telegram:
        bot_token: ""        # from @BotFather
        chat_id: ""          # your numeric chat id
"""
from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime, timezone

import requests

import db as histdb
from models import Opportunity
from scrapers.base import note_api

log = logging.getLogger(__name__)

# Telegram circuit breaker, SHARED with digest.py (same process, same
# endpoint). Rule: any endpoint that fails 3 straight times gets left
# alone for the rest of the run. Dict (not int) so digest.py mutates the
# same object. Failed alert sends still retry next run - the "alerted"
# table only records deliveries.
TG_TRIP_AFTER = 3
_tg_breaker = {"fails": 0, "announced": False}


def telegram_blocked() -> bool:
    """True once Telegram has failed TG_TRIP_AFTER straight times this run."""
    if _tg_breaker["fails"] < TG_TRIP_AFTER:
        return False
    note_api("telegram", "skipped")
    if not _tg_breaker["announced"]:
        log.warning("telegram: %d consecutive send failures - not pinging "
                    "api.telegram.org again this run", _tg_breaker["fails"])
        _tg_breaker["announced"] = True
    return True


def telegram_result(ok: bool) -> bool:
    """Record a send outcome in the shared breaker; passes `ok` through."""
    note_api("telegram", "ok" if ok else "failed")
    _tg_breaker["fails"] = 0 if ok else _tg_breaker["fails"] + 1
    return ok


def _notify_macos(title: str, body: str, sound: bool) -> bool:
    if sys.platform != "darwin":
        log.info("alert (no macOS notifier here): %s | %s", title, body)
        return False
    script = (f'display notification "{body}" with title "{title}"'
              + (' sound name "Glass"' if sound else ""))
    try:
        subprocess.run(["osascript", "-e", script], timeout=10, check=False)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("alerts: osascript failed (%s)", e)
        return False


def _send_telegram(fresh: list[Opportunity], tg: dict) -> bool:
    token, chat = tg.get("bot_token"), tg.get("chat_id")
    if not (token and chat):
        return False
    if telegram_blocked():
        return False
    lines = []
    for o in fresh[:5]:
        l, v = o.listing, o.valuation
        ltype = ("BIN+OBO" if l.listing_type == "fixed" and l.best_offer
                 else "BIN" if l.listing_type == "fixed" else "AUCTION")
        when = ""
        if ltype == "AUCTION" and l.hours_remaining is not None:
            when = f" | ends {l.hours_remaining:.1f}h"
        elif l.age_hours is not None:
            when = f" | listed {l.age_hours:.1f}h ago"
        if l.grail:
            fair = (f" vs fair ${v.fair_value:,.0f}" if v.fair_value else "")
            lines.append(
                f"GRAIL [{l.grail_score:.0f}] {l.grail} - {ltype}\n"
                f"{l.title[:80]}\n"
                f"${l.total_cost_now:,.0f}{fair}{when}\n{l.url}")
        else:
            lines.append(
                f"[{ltype}] {l.title[:80]}\n"
                f"${l.total_cost_now:,.0f} vs fair ${v.fair_value:,.0f} "
                f"(edge ${v.edge_now:,.0f}, score {v.opportunity_score:.0%}{when})\n"
                f"{l.url}")
    if len(fresh) > 5:
        lines.append(f"...and {len(fresh) - 5} more in the report")
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": "\n\n".join(lines),
                  "disable_web_page_preview": len(fresh) > 1},
            timeout=15)
        if not r.ok:
            log.warning("alerts: telegram send failed (%s)", r.text[:200])
        return telegram_result(r.ok)
    except requests.RequestException as e:
        log.warning("alerts: telegram unreachable (%s)", e)
        return telegram_result(False)


def send_alerts(opps: list[Opportunity], config: dict, db_path: str) -> int:
    acfg = config.get("alerts", {})
    if not acfg.get("enabled", True):
        return 0
    # Gate on the tradeable facts (edge $, ROI, capturability) with
    # confidence as a floor - NOT multiplied together, because soft
    # valuation sources (ask-based/guide-only) cap confidence low and
    # would silently mute everything.
    min_edge = acfg.get("min_edge_now", 150)
    min_roi = acfg.get("min_roi", 0.15)
    # too-good-to-be-true ceiling: absurd ROI = bad data, not a real deal
    max_roi = acfg.get("max_roi", 2.0)
    min_capture = acfg.get("min_capture", 0.5)
    min_conf = acfg.get("min_confidence", 0.15)
    priority_only = acfg.get("priority_only", True)

    def passes(o):
        v, l = o.valuation, o.listing
        if not (v.edge_now >= min_edge and min_roi <= v.roi <= max_roi
                and v.confidence >= min_conf
                and (l.priority or not priority_only)
                and not l.discovery
                and not any("SUSPICIOUS" in n for n in v.notes)
                # a set-wide median across different cards is not a price:
                # "Michael Jordan 1984 Star" mixes #101, #288 and #195 into
                # one $29 number. Browsable in the report, never alertable.
                and not any("MIXED POOL" in n for n in v.notes)):
            return False
        # Capture gates BINs only: a fresh underpriced BIN is real, a stale
        # one is stale for a reason. Auctions with a big edge are worth an
        # early alert even days out (you'll want to watch/snipe them), so
        # they clear on edge/ROI/confidence alone.
        if l.listing_type == "fixed":
            return v.capture >= min_capture
        return True

    hot = [o for o in opps if passes(o)]
    log.info("alerts: %d of %d rows passed gates (edge>=%s roi %s-%s "
             "conf>=%s capture>=%s priority_only=%s)", len(hot), len(opps),
             min_edge, min_roi, max_roi, min_conf, min_capture, priority_only)

    # grail alerts: a different question than profit - "is a card Andrew
    # WANTS newly available or about to close?" Bypasses every profit gate
    # (a grail at fair is still a grail) but only for actionable moments:
    # freshly-listed BINs and auctions entering the endgame. Capped and
    # sorted by significance so generic grails can't flood the phone.
    gcfg = acfg.get("grails") or {}
    if gcfg.get("enabled", True):
        fresh_h = gcfg.get("fresh_hours", 24)
        ending_h = gcfg.get("ending_hours", 24)
        cap = gcfg.get("max_per_run", 5)
        gh = []
        for o in opps:
            l = o.listing
            if not l.grail:
                continue
            if l.listing_type == "fixed":
                age = l.age_hours
                if age is not None and age <= fresh_h:
                    gh.append(o)
            else:
                hrs = l.hours_remaining
                if hrs is not None and hrs <= ending_h:
                    gh.append(o)
        gh.sort(key=lambda o: (-o.listing.grail_score,
                               o.listing.total_cost_now))
        gh = gh[:cap]
        if gh:
            log.info("alerts: %d grail candidate(s) (fresh<=%sh or "
                     "ending<=%sh)", len(gh), fresh_h, ending_h)
            hot = hot + [o for o in gh if o not in hot]

    if not hot:
        return 0

    # dedupe against previously-alerted items WITHOUT marking yet - items
    # are only recorded after a channel actually delivers, so a failed
    # send retries on the next sweep
    conn = histdb.connect(db_path)
    fresh = [o for o in hot
             if not conn.execute("SELECT 1 FROM alerts WHERE item_key=?",
                                 (o.listing.listing_id or o.listing.url,)
                                 ).fetchone()]
    if not fresh:
        conn.close()
        return 0

    # grails lead the message (by significance), then profit rows by edge
    fresh.sort(key=lambda o: (-o.listing.grail_score, -o.valuation.edge_now))
    log.info("alerts: %d fresh (not previously alerted) - sending", len(fresh))
    sent_tg = _send_telegram(fresh, acfg.get("telegram") or {})
    log.info("alerts: telegram delivered=%s", sent_tg)
    sent_mac = False
    if acfg.get("macos_notification", True) and not sent_tg:
        top = fresh[0]
        l, v = top.listing, top.valuation
        body = (f"{l.title[:70]} - ${l.total_cost_now:,.0f} vs fair "
                f"${v.fair_value:,.0f} (edge ${v.edge_now:,.0f})")
        if len(fresh) > 1:
            body += f" +{len(fresh) - 1} more in the report"
        sent_mac = _notify_macos(f"Card deal: ${v.edge_now:,.0f} edge", body,
                                 acfg.get("sound", True))
    if sent_tg or sent_mac:
        now = datetime.now(timezone.utc).isoformat()
        conn.executemany(
            "INSERT OR IGNORE INTO alerts VALUES (?,?)",
            [((o.listing.listing_id or o.listing.url), now) for o in fresh])
        conn.commit()
    conn.close()
    if not (sent_tg or sent_mac):
        return 0
    for o in fresh:
        log.info("ALERT: %s | $%.0f vs fair $%.0f | %s",
                 o.listing.title[:60], o.listing.total_cost_now,
                 o.valuation.fair_value, o.listing.url)
    return len(fresh)
