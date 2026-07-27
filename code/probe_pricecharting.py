"""Show PriceCharting resolution for real listing titles, card by card.

Run it from `Check PriceCharting.command`, or:

    .venv/bin/python -B code/probe_pricecharting.py
    .venv/bin/python -B code/probe_pricecharting.py "1948 Bowman #69 George Mikan PSA 3"

Why this exists
---------------
The valuation engine now sends a structured card identity to
`/api/products`, scores every candidate, and only accepts a product when the
match is good enough to bid on.  That decision is the single most important
one in the whole system, so it must be inspectable without reading a log.

For each title this prints the extracted identity, the search phrase we send,
every candidate PriceCharting returned with its score, which one we picked,
and the price we would read off it at that grade.

No credential is ever printed. Nothing is written to the database.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main as scanner                                    # noqa: E402
from valuation.identity import identity_of, match_band    # noqa: E402
from valuation.price_guide import PriceGuide, _guide_cents  # noqa: E402


# Real titles from Andrew's 2026-07-26 workbook: the eight Disney parallels
# that shared one $1,069.60, the Superman pool that priced a plastic figure
# at $2,821, and the Mikan rows that ignored their own grade qualifiers.
DEFAULT_TITLES = [
    "2023 Topps Chrome Disney 100 Cinderella Pink Refractor #/399 PSA 10",
    "2023 Topps Chrome Disney 100 Escape from a Plane Toy Story Black #/10 PSA 10",
    "2023 Topps Chrome Disney 100 Zurg Orange Wave Refractor #/25 PSA 10",
    "1948 Bowman #69 George Mikan Rookie RC PSA 3",
    "1948 Bowman #69 George Mikan Rookie RC PSA AUTHENTIC",
    "1999 Pokemon Base Set 1st Edition Charizard #4 Holo PSA 8.5",
    "T206 Piedmont 150 Ty Cobb Green Portrait PSA 1",
    "1940 Superman Gum R145 Superman Racing the Shells #40 PSA 6",
    'McFarlane DC Multiverse Superman Classic 1940 Animation 7" Figure',
]

# `--coverage` answers a different question: not "is our matching correct?"
# but "which of Andrew's categories can PriceCharting lead on at all?".
# 1948 Bowman basketball returned 100 comic books and no Mikan, which
# suggests their catalogue is deep on TCG and modern cards and thin on
# vintage sports. Where the guide cannot lead, comps must carry the row.
COVERAGE = {
    "vintage sports": [
        "1952 Topps #311 Mickey Mantle PSA 5",
        "1933 Goudey #53 Babe Ruth PSA 4",
        "1948 Bowman #69 George Mikan Rookie PSA 3",
        "1986 Fleer #57 Michael Jordan Rookie PSA 9",
        "1955 Topps #123 Sandy Koufax Rookie SGC 4",
    ],
    "modern sports": [
        "2003 Topps Chrome #111 LeBron James Rookie PSA 9",
        "2018 Panini Prizm #280 Luka Doncic Silver PSA 10",
        "2001 Topps Chrome Traded #T264 Ichiro Rookie PSA 10",
    ],
    "vintage pokemon": [
        "1999 Pokemon Base Set 1st Edition Charizard #4 Holo PSA 8",
        "1999 Pokemon Jungle 1st Edition Snorlax #11 Holo PSA 9",
        "1997 Topsun Charizard Green Back PSA 8",
        "2000 Pokemon Neo Genesis 1st Edition Lugia #9 Holo PSA 9",
    ],
    "sealed games": [
        "Super Mario Bros NES Sealed WATA 9.4 A",
        "The Legend of Zelda NES Sealed VGA 85",
        "Pokemon Red Version Game Boy Sealed WATA 9.0",
    ],
    "watches": [
        "Rolex Submariner 5513 stainless steel watch",
        "Patek Philippe Calatrava 4864R watch",
    ],
    "comics": [
        "Action Comics #22 1940 DC Comics CGC 4.0",
    ],
}


SPORTS_HOST = "https://www.sportscardspro.com"

# The exact cards that returned Funko POPs, LEGO sets and comic books from
# pricecharting.com on 2026-07-26. SportsCardsPro is PriceCharting's own
# sister site for sports cards, with an identical API shape - their docs
# even use "Michael Jordan #57 | Basketball Cards 1986 Fleer" as the sample.
SPORTS_TITLES = [
    "1952 Topps #311 Mickey Mantle PSA 5",
    "1986 Fleer #57 Michael Jordan Rookie PSA 9",
    "1948 Bowman #69 George Mikan Rookie PSA 3",
    "1955 Topps #123 Sandy Koufax Rookie SGC 4",
    "2003 Topps Chrome #111 LeBron James Rookie PSA 9",
    "2018 Panini Prizm #280 Luka Doncic Silver PSA 10",
]


def run_sports(guide) -> int:
    """Does the existing PriceCharting token reach SportsCardsPro?

    This is the whole question. The API shape, field names and grade ladder
    are identical, so if the token is accepted the sports gap closes with
    almost no new code. If it is refused, a separate SportsCardsPro
    subscription is needed - and that is a purchase decision, not a bug.
    """
    say("SportsCardsPro probe - PriceCharting's sister site for sports cards.")
    say(f"Host: {SPORTS_HOST}")
    say("Testing whether your existing PriceCharting token is accepted.\n")

    import requests
    try:
        r = requests.get(f"{SPORTS_HOST}/api/products",
                         params={"t": guide.pc_token,
                                 "q": "michael jordan 1986 fleer"},
                         timeout=30)
        payload = r.json() if r.content else {}
    except Exception as exc:                      # noqa: BLE001
        say(f"  request failed: {type(exc).__name__}")
        say("  Could not reach SportsCardsPro at all - check connectivity.")
        return 1

    if not isinstance(payload, dict) or payload.get("status") != "success":
        msg = (payload.get("error-message")
               if isinstance(payload, dict) else None) or f"HTTP {r.status_code}"
        say(f"  TOKEN REFUSED: {msg}")
        say("")
        say("  Your PriceCharting subscription does not cover sports data.")
        say("  SportsCardsPro is a separate subscription from the same")
        say("  company. Everything else is already built: identical")
        say("  endpoints, identical field names, identical grade ladder.")
        say("  Subscribing is the only step between here and sports")
        say("  coverage - see sportscardspro.com/sportscardspro-premium")
        return 1

    say("  TOKEN ACCEPTED - your subscription reaches SportsCardsPro.\n")
    landed = 0
    for title in SPORTS_TITLES:
        ident = identity_of(title)
        q = ident.guide_query()
        say("=" * 78)
        say(title)
        say(f"  we ask for : {q!r}")
        try:
            resp = requests.get(f"{SPORTS_HOST}/api/products",
                                params={"t": guide.pc_token, "q": q},
                                timeout=30)
            cands = (resp.json() or {}).get("products") or []
        except Exception as exc:                  # noqa: BLE001
            say(f"  request failed: {type(exc).__name__}")
            continue
        scored = sorted(
            ((ident.score_candidate(c.get("product-name", ""),
                                    c.get("console-name", "")), c)
             for c in cands if isinstance(c, dict)), key=lambda p: -p[0])
        say(f"  candidates : {len(cands)} returned")
        for score, cand in scored[:4]:
            say(f"      {score:5.0%} {match_band(score):7s} "
                f"{str(cand.get('product-name'))[:38]:38s} | "
                f"{str(cand.get('console-name'))[:26]}")
        if scored and match_band(scored[0][0]) in ("exact", "strong"):
            landed += 1
            say(f"  verdict    : LANDED - {scored[0][1].get('product-name')}")
        else:
            say("  verdict    : no confident match")
        time.sleep(1.05)          # their documented 1 call/second limit
    say("=" * 78)
    say(f"Landed {landed} of {len(SPORTS_TITLES)} sports cards.")
    say("pricecharting.com landed 0 of these. If this number is high, the")
    say("sports gap closes by pointing the guide at SportsCardsPro for")
    say("sports-card identities.")
    return 0


def run_coverage(guide) -> int:
    """Which categories can PriceCharting actually lead on?"""
    say("Coverage probe: does PriceCharting carry these categories?")
    say("Paced at one call per second; nothing is written to the database.\n")
    totals = {}
    for category, titles in COVERAGE.items():
        landed = 0
        say("=" * 78)
        say(category.upper())
        for title in titles:
            ident = identity_of(title)
            if not ident.is_specific():
                say(f"  {'too vague':>12}  {title[:56]}")
                continue
            quote = guide.quote(ident)
            if quote.landed:
                landed += 1
                say(f"  {quote.match:>12}  {title[:52]}")
                say(f"  {'':>12}  -> {quote.product_name} "
                      f"[{quote.console_name}]"
                      + (f"  ${quote.value:,.0f}" if quote.value else ""))
            else:
                say(f"  {'no match':>12}  {title[:52]}")
                say(f"  {'':>12}  -> {quote.note[:60]}")
        totals[category] = (landed, len(titles))
        say()
    say("=" * 78)
    say("COVERAGE SUMMARY")
    for category, (landed, total) in totals.items():
        bar = "#" * landed + "." * (total - landed)
        verdict = ("guide can lead" if landed >= total * 0.6
                   else "comps must carry this" if landed == 0
                   else "mixed - check row by row")
        say(f"  {category:<16} {landed}/{total}  {bar:<6}  {verdict}")
    say()
    say("Where the guide cannot lead, rows fall back to sold comps and are")
    say("marked IDENTITY UNRESOLVED - visible for browsing, never bid targets.")
    return 0


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "test results", "pricecharting_probe.txt")
_LINES: list[str] = []


def say(text: str = "") -> None:
    """Print to the window AND keep it, so the run can be reviewed later.

    A probe you can only read while it is on screen is a probe nobody can
    help you with afterwards.
    """
    print(text)
    _LINES.append(text)


def save_report() -> None:
    try:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w") as fh:
            fh.write("\n".join(_LINES) + "\n")
        print(f"\nSaved a copy of this run to:\n  test results/"
              f"{os.path.basename(REPORT)}")
    except OSError as exc:
        print(f"\n(Could not save the report: {exc})")


def main() -> int:
    config = scanner.load_config(os.path.join(ROOT, "config.yaml"))
    guide = PriceGuide(config)
    if not guide.pc_token:
        say("No PriceCharting token configured - nothing to probe.")
        return 1

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--sports" in sys.argv[1:]:
        rc = run_sports(guide)
        save_report()
        return rc
    if "--coverage" in sys.argv[1:]:
        rc = run_coverage(guide)
        save_report()
        return rc

    titles = args or DEFAULT_TITLES
    say(f"Probing {len(titles)} title(s) against PriceCharting.")
    say("Requests are paced at one per second, as the paid API requires.\n")

    landed = 0
    for title in titles:
        ident = identity_of(title)
        say("=" * 78)
        say(title)
        say(f"  identity   : class={ident.object_class} year={ident.year} "
              f"subject={' '.join(ident.subject) or '-'} "
              f"#{ident.number or '-'} parallel={ident.parallel or '-'} "
              f"serial={ident.serial or '-'} "
              f"grade={ident.grade if ident.grade is not None else '-'}"
              f"{' (' + ident.qualifier + ')' if ident.qualifier else ''}")
        say(f"  specificity: {ident.specificity():.0%}"
              f"{'  <-- too vague to bid on' if not ident.is_specific() else ''}")
        q = ident.guide_query()
        say(f"  we ask for : {q!r}")

        search = guide._cached_product(f"search:{q}", "products", {"q": q})
        cands = (search or {}).get("products") or []
        if not cands:
            say("  candidates : none returned")
            say()
            continue
        scored = sorted(
            ((ident.score_candidate(c.get("product-name", ""),
                                    c.get("console-name", "")), c)
             for c in cands if isinstance(c, dict)),
            key=lambda pair: -pair[0])
        say(f"  candidates : {len(cands)} returned, top 5 by match score")
        for score, c in scored[:5]:
            say(f"      {score:5.0%}  id={str(c.get('id')):>8}  "
                  f"{str(c.get('product-name'))[:44]:44s} | "
                  f"{str(c.get('console-name'))[:24]}")

        quote = guide.quote(ident)
        verdict = "LANDED" if quote.landed else "NOT LANDED - browse only"
        say(f"  verdict    : {verdict} ({quote.match}, {quote.score:.0%})")
        if quote.product_name:
            say(f"  product    : {quote.product_name} "
                  f"[{quote.console_name}] id={quote.product_id}")
        if quote.genre:
            say(f"  genre      : {quote.genre}")
        if quote.sales_volume:
            say(f"  liquidity  : {quote.sales_volume}/yr "
                  f"({quote.sales_volume / 12:.1f}/mo)")
        if quote.value:
            say(f"  VALUE      : ${quote.value:,.2f}   [{quote.how}]")
        else:
            say(f"  VALUE      : none - {quote.note}")
        landed += 1 if quote.landed else 0
        say()

    say("=" * 78)
    say(f"Landed {landed} of {len(titles)} title(s) on a specific product.")
    say("Titles that did not land are shown in the workbook as browsing")
    say("values only and are blocked from Today, Action and alerts.")
    save_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
