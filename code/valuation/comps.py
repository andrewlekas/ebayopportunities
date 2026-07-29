"""Robust market value from sold comparables.

Pipeline:
  1. Fuzzy title matching - drop comps that don't match the target query
     (guards against wrong card/set/grade polluting the estimate).
  2. Grade-token guard - if the query names a grade (e.g. "PSA 9"), comps
     mentioning a *different* grade are discarded.
  3. MAD outlier rejection - drop sales > k median-absolute-deviations from
     the median (kills shill bids, damaged items, lots, typos).
  4. Recency-weighted median - newer sales count more (exponential decay).
"""
from __future__ import annotations

import collections
import logging
import math
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from models import SoldComp
from textutil import fold

log = logging.getLogger(__name__)

# Broad token pattern: matches ANY "<grader> <number>" text, including
# impossible numbers. Used for STRIPPING grade text out of queries (we want
# "CGC 85" removed from a price-guide search even though 85 isn't a grade).
# Grade VALUES must come from grade_info(), which validates - see below.
GRADE_TOKEN_RE = re.compile(
    r"\b(psa|bgs|cgc|sgc|bvg|wata|vga)\s*(\d{1,3}(?:\.\d)?)\b", re.I)
# Back-compat alias: other modules import GRADE_RE to strip/detect grade text.
GRADE_RE = GRADE_TOKEN_RE
NOISE_WORDS = {"the", "a", "an", "of", "for", "and", "card", "pokemon", "tcg"}

# Real card grades are 1-10 (half points allowed). Anything above that is a
# seller typo ("CGC 85" for CGC 8.5, seen live 2026-07-25 on a $1,886 Topsun
# Charizard that the old regex read as grade 85 -> "PSA 84") or an SGC
# legacy label. Typos are treated as UNGRADED, which values the card DOWN
# (raw) rather than up - a wrong guess must never inflate fair value.
MAX_GRADE = 10.0
# SGC graded on a 100-point scale until 2020. Their own published conversion
# to the modern 1-10 scale:
SGC_LEGACY_SCALE = {
    "100": "10", "98": "10", "96": "9", "92": "8.5", "88": "8", "86": "7.5",
    "84": "7", "80": "6", "70": "5", "60": "4", "50": "3", "40": "2",
    "30": "1.5", "20": "1.5",
}
# VGA grades sealed games out of 100 (85 = NM+, 90, 95, 100 = Gem). It maps
# onto the familiar 1-10 scale by dividing by ten, so a VGA 85 sits with a
# WATA 8.5 rather than being read as "grade 85".
VGA_SCALE_DIVISOR = 10.0
# Grade tokens this run refused to parse - logged at the end of the scan so
# a new seller convention shows up as evidence instead of a silent misprice.
UNPARSEABLE_GRADES: collections.Counter = collections.Counter()


def _normalize_grade(grader: str, num: str) -> str | None:
    """Raw grade label -> modern 1-10 grade string, or None if impossible.

    None means "this title has no trustworthy grade" and the caller should
    treat the item as ungraded (UNGRADED_GRADE), never as the raw number.
    """
    try:
        val = float(num)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    if val <= MAX_GRADE:
        return f"{val:g}"
    if grader.lower() == "vga":
        scaled = val / VGA_SCALE_DIVISOR
        if 0 < scaled <= MAX_GRADE:
            return f"{scaled:g}"
    if grader.lower() == "sgc":
        mapped = SGC_LEGACY_SCALE.get(str(num).split(".")[0])
        if mapped:
            return mapped
    UNPARSEABLE_GRADES[f"{grader.upper()} {num}"] += 1
    return None

# Cross-grader normalization:
# 1. GRADE_SHIFT: every non-PSA grader counts one point lower than PSA -
#    a CGC/BGS/SGC/BVG 9 is treated as a PSA 8 equivalent for matching
#    AND valuation.
# 2. GRADER_PREMIUM: residual price multiplier at the same EFFECTIVE grade
#    (all 1.0 now; the shift prices the discount).
# Override via algorithm.grader_grade_shift / grader_premiums in config.
# WATA and VGA grade SEALED VIDEO GAMES, not cards - they are never
# cross-referenced against PSA, so no shift applies to them.
GRADE_SHIFT = {"cgc": -1.0, "bgs": -1.0, "sgc": -1.0, "bvg": -1.0,
               "wata": 0.0, "vga": 0.0}
GRADER_PREMIUM = {"psa": 1.00, "bgs": 1.00, "cgc": 1.00,
                  "sgc": 1.00, "bvg": 1.00, "wata": 1.00, "vga": 1.00}
# 3. UNGRADED_GRADE: a title with NO grade token is a raw card, assumed
#    equivalent to PSA 5 (Andrew's rule). Raw listings/comps therefore
#    only match each other and PSA-5-equivalent grades - never a PSA 9
#    query. Override via algorithm.ungraded_grade in config.
UNGRADED_GRADE = 5.0


def grader_of(title: str) -> str | None:
    gi = grade_info(title)
    return gi[0] if gi else None


def _effective(grader: str, num: str) -> str:
    """Grade number adjusted to PSA-equivalent terms ('9' for CGC 10).
    Floored at 1 - there is no PSA 0; a CGC 1 is a PSA 1 equivalent."""
    val = max(1.0, float(num) + GRADE_SHIFT.get(grader.lower(), 0.0))
    return f"{val:g}"


def grade_info(title: str):
    """(grader, grade, effective_grade) or None.

    `grade` is the NORMALIZED modern grade (an SGC 92 label reports as
    '8.5'); `effective_grade` additionally applies the cross-grader shift,
    so it is always in PSA-equivalent terms. Impossible grade tokens are
    skipped entirely - the title reads as ungraded.
    """
    for m in GRADE_TOKEN_RE.finditer(title):
        norm = _normalize_grade(m.group(1), m.group(2))
        if norm is None:
            continue
        return m.group(1).lower(), norm, _effective(m.group(1), norm)
    return None


def _tokens(text: str) -> set[str]:
    # Grader-agnostic with grade shift: "PSA 9" and "CGC 10" both become
    # the token "grade9" so equivalent grades match across companies.
    # Unparseable grade tokens are dropped rather than tokenized, so a
    # typo'd "CGC 85" can never match another "CGC 85".
    def _repl(m):
        norm = _normalize_grade(m.group(1), m.group(2))
        return f" grade{_effective(m.group(1), norm)} " if norm else " "

    text = GRADE_TOKEN_RE.sub(_repl, fold(text).lower())
    return {w for w in re.findall(r"[a-z0-9#/.']+", text)
            if w not in NOISE_WORDS}


def title_match_score(query: str, title: str) -> float:
    """0..1 blend of token overlap and sequence similarity."""
    qt, tt = _tokens(query), _tokens(title)
    if not qt:
        return 0.0
    coverage = len(qt & tt) / len(qt)               # query tokens found in title
    seq = SequenceMatcher(None, fold(query).lower(),
                          fold(title).lower()).ratio()
    return 0.7 * coverage + 0.3 * seq


# foreign-language version markers: these cards price very differently
# from the English versions our comps/values assume
FOREIGN_RE = re.compile(
    r"\b(germany?|deutsch(land)?|karte|1\.\s?edition|french|francais|français|"
    r"carte|spanish|espanol|español|edicion|edición|italian|italiano|carta|"
    r"edizione|portuguese|português|dutch|korean|chinese|"
    # foreign-language Pokemon names (German/French cards titled natively)
    r"glurak|turtok|bisaflor|dracaufeu|tortank|florizarre|evoli)\b", re.I)
JP_RE = re.compile(r"\b(japanese|japan|jpn)\b", re.I)
# queries where Japanese versions ARE the target
JP_NATIVE_RE = re.compile(r"topsun|carddass|no rarity|japanese|旧裏|マークなし", re.I)


def language_conflict(query: str, title: str) -> bool:
    """True if the listing/comp is a foreign-language version the query's
    valuation doesn't apply to."""
    if FOREIGN_RE.search(title):
        return True
    if JP_RE.search(title) and not JP_NATIVE_RE.search(query):
        return True
    return False


def _holo(text: str) -> str | None:
    t = text.lower()
    if re.search(r"non[\s-]?holo|non[\s-]?foil", t):
        return "nonholo"
    if re.search(r"reverse\s*(holo|foil)|rev\s*holo", t):
        return "reverse"
    if re.search(r"\bholo\b|\bholographic\b|\bfoil\b", t):
        return "holo"
    return None


def _edition(text: str) -> str | None:
    t = text.lower()
    if re.search(r"1st\s*ed|first\s*ed", t):
        return "1st"
    if re.search(r"\bunlimited\b", t):
        return "unlimited"
    if re.search(r"\bshadowless\b", t):
        return "shadowless"
    return None


def _topsun_series(text: str) -> str | None:
    """Topsun (1995/1997 originals) vs Topsun VS (1998-99 'X VS Y' battle
    series) - different series at very different prices. Only meaningful
    when the text is actually about Topsun; None otherwise so plain 'vs'
    in unrelated titles never triggers."""
    t = text.lower()
    if not re.search(r"top[\s-]?sun|トップサン", t):
        return None
    return "vs" if re.search(r"\bvs\.?\b|\bversus\b", t) else "original"


# Tokens that describe the CONTEXT of a card (set, grade, era, format)
# rather than its SUBJECT (the player/character). Fuzzy title match can
# score high on context alone - "1st Edition Fossil Holo PSA 9" matches a
# Magneton listing against a Gengar query. The subject guard fixes that.
GENERIC_TOKENS = {
    # grading / condition
    "psa", "bgs", "cgc", "sgc", "bvg", "gem", "mint", "nm", "graded",
    "grade", "slab", "authentic", "cert", "pop",
    # card-world context
    "card", "cards", "rookie", "rc", "auto", "autograph", "signed",
    "holo", "holographic", "foil", "reverse", "rare", "error", "promo",
    "1st", "first", "edition", "ed", "unlimited", "shadowless", "vintage",
    "sealed", "pack", "packs", "booster", "box", "wax", "japanese",
    "english", "movie", "jersey", "patch", "flag", "series",
    # franchises / sets / brands (context, not subject)
    "pokemon", "topps", "fleer", "bowman", "goudey", "star", "prizm",
    "chrome", "exquisite", "contenders", "immaculate", "treasures",
    "national", "upper", "deck", "select", "optic", "mosaic", "donruss",
    "panini", "leaf", "sporting", "news", "base", "set", "jungle",
    "fossil", "topsun", "carddass", "expedition", "celebrations",
    "sports", "stars",
    # game-world context
    "nes", "snes", "n64", "gb", "gameboy", "wata", "vga", "cib",
    # filler + ordinal fragments ("1st" tokenizes to "st")
    "no", "rarity", "the", "of", "and", "vs", "us", "open",
    "st", "nd", "rd", "th",
    # --- added 2026-07-25 -------------------------------------------------
    # Everything below was being injected as a card's "subject" on
    # subject-less queries. Measured over 599 real listings, the most
    # common injected "subjects" were: symbol x116, wotc x67, tcg x51,
    # lp x20, stamp x15, near x12, hp x10, lot x10 - none of which name
    # a card. See _subject_tokens.
    "tcg", "ptcg", "wotc", "symbol", "stamp", "stamped", "thin", "thick",
    "swirl", "lot", "bundle", "single", "singles", "gym", "neo", "team",
    "non", "reverse", "unlimited", "shadowed", "misprint", "miscut",
    # condition / grading chatter
    "nm", "lp", "mp", "hp", "dmg", "played", "near", "excellent", "good",
    "poor", "fair", "pristine", "raw", "ungraded", "centered", "centering",
    "condition", "psadna", "cert", "certified", "authenticated",
    "authentic", "genuine", "original", "official",
    # seller marketing - these are LONG words, so the old "pick the three
    # longest tokens" rule chose them over the actual card name
    "collectible", "collectable", "collection", "investment", "invest",
    "beautiful", "gorgeous", "stunning", "amazing", "incredible", "insane",
    "perfect", "flawless", "clean", "sharp", "crisp", "high", "grade",
    "low", "pop", "population", "scarce", "hot", "key", "iconic", "grail",
    "deal", "sale", "read", "look", "wow", "l@@k", "must", "see", "nice",
    "super", "ultra", "mega", "premium", "quality", "value", "bargain",
    "estate", "find", "vault", "portfolio", "graded", "slabbed",
    # publishers, set names and packaging that read like subjects
    "wizards", "coast", "nintendo", "creatures", "rocket", "trainer",
    "energy", "factory", "sealed", "unweighed", "weighed", "resealed",
    "new", "old", "vtg", "htf", "oop", "rare", "ultra", "secret",
    "art", "alt", "common", "uncommon", "heroes", "challenge", "legendary",
    "aquapolis", "skyridge", "genesis", "discovery", "revelation",
}


def _subject_tokens(query: str) -> set[str]:
    """Query tokens that name the actual subject (player, character,
    thing) - everything left after stripping context words and numbers."""
    toks = re.findall(r"[a-z]+", fold(query).lower())
    return {t for t in toks if t not in GENERIC_TOKENS and len(t) > 1}


def subject_candidates(title: str, limit: int = 2) -> list[str]:
    """Best guesses at the SUBJECT of a listing title, most likely first.

    Used when a watchlist query names no subject of its own ("1999 1st
    Edition Pokemon Set") and each listing has to be valued against its
    own card rather than a mongrel set-wide median.

    The old rule was "the three longest leftover words", which reliably
    picked seller adjectives - a Jungle Snorlax was valued against
    'investment beautiful centering'. Now: drop context/marketing words
    (GENERIC_TOKENS above), require a real word (>= 3 letters, so 'lp',
    'hp' and 'nm' can't win but 'Mew' still can), and prefer the token
    that appears EARLIEST in the title - card names lead the descriptive
    part of a title, while adjectives get appended to the end.
    """
    seen: dict[str, int] = {}
    for i, tok in enumerate(re.findall(r"[a-z]+", fold(title).lower())):
        if len(tok) < 3 or tok in GENERIC_TOKENS:
            continue
        seen.setdefault(tok, i)
    return [t for t, _ in sorted(seen.items(), key=lambda kv: kv[1])][:limit]


def subject_missing(query: str, title: str) -> bool:
    """True when the query names a subject and the title shows NONE of it.
    Queries that are all context ("1999 1st Edition Pokemon Set") skip
    the check."""
    subj = _subject_tokens(query)
    if not subj:
        return False
    tt = set(re.findall(r"[a-z]+", fold(title).lower()))
    return not (subj & tt)


def _completeness(text: str) -> str | None:
    """Sealed vs complete-in-box vs loose - for VIDEO GAMES the single
    biggest price factor. A sealed graded copy and a loose cartridge of the
    same title differ by 100x, and both used to land in the same pool
    (every game query in the watchlist valued between $9 and $70 because
    loose carts set the median)."""
    t = text.lower()
    if re.search(r"\b(factory\s*)?sealed\b|\bnew in (box|package)\b|\bnib\b"
                 r"|\bshrink ?wrap", t):
        return "sealed"
    if re.search(r"\bcib\b|complete in box|\bwith (box|manual)\b"
                 r"|\bbox (and|&) manual\b", t):
        return "cib"
    if re.search(r"\bloose\b|\bcart(ridge)? only\b|\bdisc only\b"
                 r"|\bgame only\b", t):
        return "loose"
    return None


YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
# Card numbers: "#101", "No. 53", Pokemon's "4/102". Deliberately NOT bare
# digits - years, grades and pop counts would all qualify.
CARD_NUMBER_RE = re.compile(
    r"#\s*(\d{1,4})(?![\d/])|\bno\.?\s*(\d{1,4})\b|\b(\d{1,3})\s*/\s*\d{1,3}\b",
    re.I)


def years_in(text: str) -> set[str]:
    """Four-digit years, plus the expansion of ranges like '1984-85'."""
    folded = fold(text)
    found = set(YEAR_RE.findall(folded))
    for m in re.finditer(r"\b(1[89]\d{2}|20\d{2})\s*[-/]\s*(\d{2})\b", folded):
        start = int(m.group(1))
        suffix = int(m.group(2))
        # Sports seasons cross centuries: 1999-00 means 1999 and 2000, not
        # 1900. Build the end year in the start year's century, then roll it
        # forward when the suffix wrapped.
        end = (start // 100) * 100 + suffix
        if end < start:
            end += 100
        found.add(str(end))
    return found


def year_conflict(query: str, title: str) -> bool:
    """True when the query pins a year and the title shows none of them.

    A '1948 Bowman' query was being priced by '2009-10 Bowman 48 George
    Mikan' tribute cards at $1; 'Babe Ruth 1933' had a median comp of $6
    because the pool was full of 1991/2004 reprint-era singles.
    """
    qy = years_in(query)
    if not qy:
        return False
    return not (qy & years_in(title))


def card_number(text: str) -> str | None:
    """The card's number within its set, or None."""
    folded = fold(text)
    m = CARD_NUMBER_RE.search(folded)
    if not m:
        return None
    # ``4/102`` is a Pokemon set position; ``17/75`` on a sports card is the
    # copy's serial stamp. The old generic slash rule treated both as card
    # numbers, which turned Exquisite serials into fictitious #17/#5 cards.
    if m.group(3):
        tcg_context = re.search(
            r"\b(pokemon|charizard|pikachu|blastoise|venusaur|holo|"
            r"carddass|topsun|base\s+set|jungle|fossil|rocket)\b",
            folded, re.I)
        if not tcg_context:
            return None
    num = next((g for g in m.groups() if g), None)
    if num is None:
        return None
    # a 4-digit "number" is almost always a year or a serial (/1948)
    return None if len(num) == 4 and YEAR_RE.fullmatch(num) else num


def number_conflict(query: str, title: str) -> bool:
    """True when both sides name a card number and they differ.

    'Michael Jordan 1984 Star' pools #101, #288, #195, #7 and #26 into one
    $29 median - five different cards spanning $550 to $16,800.

    Note the asymmetry: once the query names a number, a comp naming NO
    number is rejected too. An unnumbered sale cannot price a specific
    card, and admitting them is what held the median at $29 even after the
    wrong numbers were removed. A query with no number is unaffected, so
    this only bites where we have deliberately asked about one card.
    """
    q = card_number(query)
    if q is None:
        return False
    return card_number(title) != q


def variant_conflict(query: str, title: str) -> bool:
    """True when a card-defining variant the QUERY specifies differs in the
    title: holo vs non-holo vs reverse, 1st edition vs unlimited vs
    shadowless, and sealed vs CIB vs loose for games. These are different
    items at very different prices - the #1 way a cheap listing gets a rich
    card's fair value.
    """
    for fn in (_holo, _edition, _topsun_series, _completeness):
        q, t = fn(query), fn(title)
        if q is not None and t is not None and q != t:
            return True
    return False


def grade_conflict(query: str, title: str, *,
                   assume_ungraded: bool = False) -> bool:
    """True if the title's EFFECTIVE grade differs from the query's.

    Grade shift applies: a "PSA 9" query matches CGC 10 / BGS 10 (their
    9s count as PSA 8s and conflict).

    assume_ungraded=True (comp matching): a side with NO grade token is a
    raw card counted as PSA UNGRADED_GRADE (5) - so raw comps never price
    a PSA 9 query and graded comps never price a raw one. The default
    (False, listing relevance filtering) keeps the old permissive
    behavior: a broad ungraded watchlist query still SURFACES graded
    listings - the engine then values each listing at its own grade.
    """
    q = grade_info(query)
    t = grade_info(title)
    if not assume_ungraded and (not q or not t):
        return False
    ungraded_eff = f"{UNGRADED_GRADE:g}"
    q_eff = q[2] if q else ungraded_eff
    t_eff = t[2] if t else ungraded_eff
    return q_eff != t_eff


def _weighted_median(values: list[float], weights: list[float]) -> float:
    pairs = sorted(zip(values, weights))
    total = sum(weights)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]


def comp_velocity(query: str, comps: list[SoldComp], *,
                  min_match: float = 0.55) -> float | None:
    """Estimated SALES PER MONTH for this query - the liquidity dimension.

    Uses the same relevance filters as valuation. Date handling per source:
    130point comps carry real sold dates (count last 45 days); eBay sold-page
    comps are stamped with fetch time, but eBay's sold search only covers
    ~90 days, so matched-count / 3 is the honest monthly rate there.
    """
    now = datetime.now(timezone.utc)
    dated_recent = 0
    undated_total = 0
    any_real_dates = False
    for c in comps:
        if c.price <= 0 or grade_conflict(query, c.title,
                                          assume_ungraded=True):
            continue
        if language_conflict(query, c.title) or variant_conflict(query, c.title):
            continue
        if subject_missing(query, c.title):
            continue
        if year_conflict(query, c.title) or number_conflict(query, c.title):
            continue
        if title_match_score(query, c.title) < min_match:
            continue
        if c.site == "130point" and c.sold_date:
            any_real_dates = True
            if (now - c.sold_date).total_seconds() <= 45 * 86400:
                dated_recent += 1
        else:
            undated_total += 1
    if any_real_dates:
        return round(dated_recent / 1.5, 2)
    if undated_total:
        return round(undated_total / 3.0, 2)
    return None


def robust_comp_value(query: str, comps: list[SoldComp], *,
                      min_match: float = 0.55,
                      mad_k: float = 3.0,
                      half_life_days: float = 30.0,
                      premiums: dict | None = None):
    """Return (value, n_used, dispersion, avg_match) or (None, 0, 0, 0).

    When the query names a grade, comp prices are normalized to
    PSA-equivalents (a CGC 9 sale at $800 counts as a $1000 PSA-9 comp at
    the default 80% premium), so fair value is expressed in PSA terms.
    """
    now = datetime.now(timezone.utc)
    graded_query = grade_info(query) is not None
    prem = premiums or GRADER_PREMIUM
    kept: list[tuple[float, float, float]] = []  # (price, weight, match)
    for c in comps:
        if c.price <= 0 or grade_conflict(query, c.title,
                                          assume_ungraded=True):
            continue
        if language_conflict(query, c.title):
            continue
        if variant_conflict(query, c.title):
            continue
        if subject_missing(query, c.title):
            continue
        if year_conflict(query, c.title):
            continue
        if number_conflict(query, c.title):
            continue
        m = title_match_score(query, c.title)
        if m < min_match:
            continue
        price = c.price
        if graded_query:
            g = grader_of(c.title)
            factor = prem.get(g, 1.0) if g else 1.0
            price = c.price / max(factor, 0.01)
        age_days = ((now - c.sold_date).total_seconds() / 86400
                    if c.sold_date else half_life_days)
        recency_w = math.exp(-math.log(2) * age_days / half_life_days)
        kept.append((price, recency_w * m, m))
    if len(kept) < 3:
        return None, len(kept), 0.0, 0.0

    prices = [p for p, _, _ in kept]
    med = sorted(prices)[len(prices) // 2]
    mad = sorted(abs(p - med) for p in prices)[len(prices) // 2] or med * 0.05
    filtered = [(p, w, m) for p, w, m in kept if abs(p - med) <= mad_k * mad]
    if len(filtered) < 3:
        filtered = kept

    prices = [p for p, _, _ in filtered]
    weights = [w for _, w, _ in filtered]
    value = _weighted_median(prices, weights)
    med2 = sorted(prices)[len(prices) // 2]
    mad2 = sorted(abs(p - med2) for p in prices)[len(prices) // 2]
    dispersion = (mad2 / med2) if med2 else 0.0
    avg_match = sum(m for _, _, m in filtered) / len(filtered)
    return value, len(filtered), dispersion, avg_match
