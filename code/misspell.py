"""Misspelling hunter: find listings whose titles are typo'd.

Misspelled listings don't surface in normal searches, get few bidders, and
sell under market - classic structural mispricing. For each priority query
we generate typo variants of the distinctive name tokens (known hobby
misspellings first, then algorithmic drops/swaps) and search those too.
Finds are valued against the CORRECTLY spelled card's fair value.
"""
from __future__ import annotations

import re

# curated hobby misspellings (seen in real listings)
KNOWN = {
    "charizard": ["charzard", "charazard", "charizrd", "charizar"],
    "blastoise": ["blastois", "blastoice", "blastose"],
    "venusaur": ["venasaur", "venusar", "venusuar"],
    "gyarados": ["gyrados", "gyarodos", "garydos"],
    "pikachu": ["picachu", "pikachue", "pikchu"],
    "poliwrath": ["polywrath", "poliwrat"],
    "gengar": ["gengor", "genger"],
    "dragonite": ["dragonight", "draginite"],
    "snorlax": ["snorelax", "snorlaxx"],
    "vaporeon": ["vaporean", "vaporion"],
    "jolteon": ["joltean", "joltion"],
    "flareon": ["flarean", "flarion"],
    "raichu": ["riachu", "raichue"],
    "chansey": ["chansy", "chancey"],
    "pidgeotto": ["pidgeoto", "pigeotto"],
    "topsun": ["top sun", "topsum"],
    "edition": ["addition", "edtion"],
    "illustrator": ["ilustrator", "illustator"],
    "wembanyama": ["wembanyana", "wembenyama"],
    "nicklaus": ["nicklas", "niklaus"],
}

STOP = {"base", "set", "holo", "1st", "1999", "psa", "bgs", "cgc", "sgc",
        "the", "and", "pokemon", "jungle", "fossil", "rookie", "auto"}


def _algorithmic(word: str, n: int = 3) -> list[str]:
    """A few plausible typos: dropped letters and adjacent swaps."""
    out = []
    for i in range(1, len(word)):                    # dropped letter
        out.append(word[:i] + word[i + 1:])
    for i in range(1, len(word) - 1):                # adjacent swap
        out.append(word[:i] + word[i + 1] + word[i] + word[i + 2:])
    seen, uniq = set(), []
    for w in out:
        if w != word and w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq[:n]


def variant_queries(query: str, max_variants: int = 5) -> list[str]:
    """Typo'd versions of the query, most-likely misspellings first."""
    tokens = re.findall(r"[A-Za-z']+", query)
    out: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low in STOP or len(low) < 6:
            continue
        variants = KNOWN.get(low, []) or _algorithmic(low)
        for var in variants:
            vq = re.sub(rf"\b{re.escape(tok)}\b", var, query, flags=re.I)
            if vq.lower() != query.lower() and vq not in out:
                out.append(vq)
    # "1st edition" -> "1st addition" applies to many cards; keep it last
    # so name typos (rarer, richer) get budget first
    out.sort(key=lambda q: "addition" in q.lower())
    return out[:max_variants]
