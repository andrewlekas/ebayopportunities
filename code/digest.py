"""Post-scan Telegram digest: the report's greatest hits, on the phone.

After every FULL scan (not the 30-minute BIN sweeps - that would be spam),
sends two messages to the same Telegram chat as the alerts:

  1. Top N profit opportunities (by opportunity score; excludes discovery
     rows and the Watches category - watch valuations are less reliable
     and stay quarantined in their report tab)
  2. Top N grails (by significance, then price)

Messages are compact HTML with hyperlinked titles, chunked under
Telegram's 4096-char limit. Config:

    alerts:
      digest:
        enabled: true
        top_opportunities: 25
        top_grails: 25
"""
from __future__ import annotations

import html
import logging

import requests

from models import Opportunity
from security import redact_text

log = logging.getLogger(__name__)

TG_LIMIT = 3900          # headroom under Telegram's 4096


def _chunks(lines: list[str], header: str) -> list[str]:
    out, cur = [], header
    for ln in lines:
        if len(cur) + len(ln) + 2 > TG_LIMIT:
            out.append(cur)
            cur = ln
        else:
            cur = cur + "\n\n" + ln if cur else ln
    if cur:
        out.append(cur)
    return out


def _send(text: str, tg: dict) -> bool:
    # shares alerts.py's Telegram breaker: 3 straight failures anywhere
    # (alerts or digest) and we stop pinging api.telegram.org this run
    from alerts import telegram_blocked, telegram_result
    if telegram_blocked():
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
            json={"chat_id": tg["chat_id"], "text": text,
                  "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15)
        if not r.ok:
            log.warning("digest: telegram send failed (%s)",
                        redact_text(r.text[:200]))
        return telegram_result(r.ok)
    except requests.RequestException as e:
        log.warning("digest: telegram unreachable (%s)", redact_text(e))
        return telegram_result(False)


def _link(l) -> str:
    title = html.escape(l.title[:60])
    return f'<a href="{html.escape(l.url, quote=True)}">{title}</a>' \
        if l.url else title


def _profit_line(i: int, o: Opportunity) -> str:
    l, v = o.listing, o.valuation
    ltype = ("OBO" if l.listing_type == "fixed" and l.best_offer
             else "BIN" if l.listing_type == "fixed" else "AUC")
    when = ""
    if ltype == "AUC" and l.hours_remaining is not None:
        when = f", ends {l.hours_remaining:.0f}h"
    elif l.age_hours is not None and l.age_hours <= 48:
        when = f", listed {l.age_hours:.0f}h ago"
    vel = (f", {v.annualized_roi:.0%}/yr" if v.annualized_roi else "")
    return (f"{i}. {_link(l)}\n"
            f"   {ltype} ${l.total_cost_now:,.0f} vs ${v.fair_value:,.0f} | "
            f"EV ${v.expected_value:,.0f} ({v.roi:.0%}{vel}){when}")


def _grail_line(i: int, o: Opportunity) -> str:
    l, v = o.listing, o.valuation
    ltype = "BIN" if l.listing_type == "fixed" else "AUC"
    when = ""
    if ltype == "AUC" and l.hours_remaining is not None:
        when = f", ends {l.hours_remaining:.0f}h"
    fair = f" vs ${v.fair_value:,.0f}" if v.fair_value else ""
    return (f"{i}. [{l.grail_score:.0f}] {html.escape(l.grail)} - {_link(l)}\n"
            f"   {ltype} ${l.total_cost_now:,.0f}{fair}{when}")


def send_digest(opps: list[Opportunity], config: dict,
                watch_queries: set[str] | None = None) -> int:
    """Returns number of Telegram messages sent."""
    acfg = config.get("alerts", {})
    dcfg = acfg.get("digest") or {}
    if not dcfg.get("enabled", True):
        return 0
    tg = acfg.get("telegram") or {}
    if not (tg.get("bot_token") and tg.get("chat_id")):
        return 0
    n_opp = dcfg.get("top_opportunities", 25)
    n_grail = dcfg.get("top_grails", 25)
    watch_queries = watch_queries or set()

    sent = 0
    profit = sorted(
        (o for o in opps
         if not o.listing.discovery
         and o.listing.query not in watch_queries
         # mixed-pool rows carry a set-wide median, not a price for the
         # card in front of you - they stay out of the phone digest
         and not any("MIXED POOL" in n for n in o.valuation.notes)
         and o.valuation.expected_value > 0),
        key=lambda o: o.valuation.opportunity_score, reverse=True)[:n_opp]
    if profit:
        lines = [_profit_line(i, o) for i, o in enumerate(profit, 1)]
        for msg in _chunks(lines, f"<b>Top {len(profit)} opportunities</b>"):
            sent += _send(msg, tg)

    # grail section = LIVE AUCTIONS by grail significance (Andrew wants to
    # know which grails are up for bid right now, ranked by how much he
    # wants them; ties break by soonest ending)
    grails = sorted(
        (o for o in opps
         if o.listing.grail and o.listing.listing_type == "auction"
         and (o.listing.hours_remaining is None
              or o.listing.hours_remaining > 0)),
        key=lambda o: (-o.listing.grail_score,
                       o.listing.hours_remaining
                       if o.listing.hours_remaining is not None
                       else 1e9))[:n_grail]
    if grails:
        lines = [_grail_line(i, o) for i, o in enumerate(grails, 1)]
        for msg in _chunks(lines,
                           f"<b>Top {len(grails)} grail auctions (live)</b>"):
            sent += _send(msg, tg)
    return sent
