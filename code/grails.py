"""Grail hunt: the PERSONAL collection dimension of the scan.

The profit scanner asks "is this cheap?". The grail hunt asks "is this one
of the cards Andrew actually wants to own?" - a different question with its
own score. Grails live in config.yaml under `grails:`, listed in order of
significance; grail_score runs from 100 (top of the list) down to 40 (the
bottom). An entry can also be a dict to override the rank-based weight or
cap the price:

    grails:
      - Michael Jordan 1986 Fleer Auto            # rank -> score
      - {query: Babe Ruth 1933, weight: 95, max_price: 20000}

During full scans every grail is searched as its own (soft-valued,
alert-quarantined) query, AND every listing from every query is checked
against the grail list - so a grail surfacing under a regular watchlist
search still gets tagged. Matches land in the report's Grails tab sorted
by significance, regardless of expected value: a grail at fair price is
still a grail.

Matching: every token of the grail name must appear in the listing title
(with small synonym sets, e.g. auto == autograph == signed). Deliberately
strict AND-matching - grails are specific things; false positives would
bury the tab.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from textutil import fold

# multi-word phrases normalized before tokenizing, both in grail names and
# listing titles, so "Game Boy" == "GB" and "First Edition" == "1st Edition"
PHRASES = [
    ("game boy", "gameboy"),
    ("first edition", "1st edition"),
    ("1st ed.", "1st edition"),
]
_SYN_SETS = [
    {"auto", "autograph", "autographed", "autographs", "signed", "signature"},
    {"1st", "first"},
    {"gb", "gameboy"},
    {"pack", "booster", "packs"},
]
# bidirectional: any member of a set maps to the whole set
SYNONYMS = {word: s for s in _SYN_SETS for word in s}
TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    # fold accents first: "Pokémon" must tokenize as one word, otherwise a
    # grail whose name contains it can never match an accented title
    t = fold(text).lower()
    for a, b in PHRASES:
        t = t.replace(a, b)
    return TOKEN_RE.findall(t)


@dataclass
class Grail:
    name: str
    score: float
    groups: list          # list[set[str]] - each group must hit the title
    max_price: float | None = None


def load_grails(config: dict) -> list[Grail]:
    raw = config.get("grails") or []
    n = len(raw)
    out = []
    for i, entry in enumerate(raw):
        if isinstance(entry, dict):
            name = str(entry.get("query") or entry.get("name") or "").strip()
            weight = entry.get("weight")
            max_price = entry.get("max_price")
        else:
            name, weight, max_price = str(entry).strip(), None, None
        if not name:
            continue
        score = (float(weight) if weight is not None
                 else round(100 - 60.0 * i / max(n - 1, 1), 1))
        groups = [SYNONYMS.get(t, {t}) for t in _tokens(name)]
        if groups:
            out.append(Grail(name=name, score=score, groups=groups,
                             max_price=max_price))
    return out


def match(grails: list[Grail], title: str) -> Grail | None:
    """Most significant grail whose every token group hits the title."""
    tt = set(_tokens(title))
    best = None
    for g in grails:
        if all(group & tt for group in g.groups):
            if best is None or g.score > best.score:
                best = g
    return best
