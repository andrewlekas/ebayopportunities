"""Structured identity for the physical object in a listing.

WHY THIS EXISTS
---------------
Until 2026-07-26 a fair value was a function of the *watchlist query string*,
not of the card.  `fair_value(query, comps, asks)` never received the listing.
The only thing that could make a value listing-specific was
`engine._valuation_query`, which overrode the query on three ad-hoc triggers:
grade differs, query names no subject, or the listing names a card number the
query doesn't.

That produced, in one real run:

  * eight 2023 Topps Chrome Disney parallels - a /10 Black, a /99 Dual Auto,
    a /25 Orange Wave, a /399 Pink - all valued at exactly $1,069.60, because
    parallel and serial were not triggers;
  * six "Superman 1940" rows all at $2,821.29, including a wax-pack wrapper,
    a novelty coupon, Action Comics #22 and an $18 McFarlane plastic figure,
    because object class was not a concept;
  * five George Mikan rows all at $1,529.25, because "PSA Authentic" (trimmed
    or altered) normalises to the same PSA-5-equivalent as a raw copy.

Across the whole workbook, 489 rows carried just 5 distinct valuations.

The fix is to describe the OBJECT, then value that.  Two listings may share a
fair value only when their identities agree on every field that moves price.

DESIGN NOTES
------------
* Extraction is deliberately conservative.  An unrecognised parallel yields
  None (unknown), never a guess.  Unknown fields lower `specificity()`, which
  is what gates a row out of Action - so the failure mode is a quiet workbook,
  not a confident wrong number.
* `fingerprint()` is the grouping key.  If two listings share a fingerprint
  they ARE the same tradeable asset and may share evidence.
* `conflicts_with()` is the comp/candidate filter: it answers "why are these
  different assets?" in words, so notes and audit trails stay readable.
* This module owns no network and no config.  It is pure text -> structure,
  which keeps it fast (it runs on every one of ~7,000 listings per scan) and
  trivially testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from functools import lru_cache

from . import comps as comps_mod
from .comps import (card_number, grade_info, grader_of, subject_candidates,
                    title_match_score, years_in)


# --------------------------------------------------------------------------
# Object class
# --------------------------------------------------------------------------
# Order matters: the first pattern that matches wins, so the narrow and
# dangerous classes (watch components, wrappers, figures) are tested before
# the broad ones.  A $2,821 fair value on a 7-inch plastic Superman happened
# because "figure" was not a class the system could even express.

WATCH_PART_RE = re.compile(
    r"\b(dial|buckle|clasp|strap|band|bracelet|bezel|crown|movement|"
    r"caseback|case\s*back|hands?|crystal|link|lugs?)\b"
    r"|\bfor\s+(?:restoration|parts|repair)\b"
    r"|\b(?:parts?|spares)\s+only\b", re.I)

WATCH_RE = re.compile(
    r"\b(rolex|patek\s*philippe|audemars\s*piguet|vacheron|jaeger|"
    r"lecoultre|omega|cartier|tudor|breitling|iwc|panerai|hublot|"
    r"tag\s*heuer|longines|zenith|grand\s*seiko|a\.?\s*lange)\b", re.I)

FIGURE_RE = re.compile(
    r"\b(mcfarlane|funko|pop!|figurine|action\s*figure|statue|bobblehead|"
    r"bobble\s*head|diorama|plush|lego|minifig)\b"
    r"|\b\d+(?:\.\d+)?[\"”]?\s*(?:inch\s*)?figure\b", re.I)

WRAPPER_RE = re.compile(
    r"\b(wrapper|wax\s*pack|cello\s*pack|cello|rack\s*pack|blister|"
    r"empty\s*box|box\s*only|display\s*box|tin\s*only)\b", re.I)

PACK_RE = re.compile(
    r"\b(booster\s*(?:pack|box)|sealed\s*(?:pack|box|case)|unopened|"
    r"factory\s*sealed|hobby\s*box|blaster|mega\s*box|etb|"
    r"elite\s*trainer)\b", re.I)

COUPON_RE = re.compile(
    r"\b(coupon|premium\s*(?:offer|redemption)?|redemption\s*(?:slip|form)|"
    r"order\s*form|advertisement|ad\s*slick|catalog)\b", re.I)

COMIC_RE = re.compile(
    r"\b(action\s*comics|detective\s*comics|marvel|dc\s*comics|"
    r"amazing\s*fantasy|amazing\s*spider[-\s]*man|comic\s*book|comics?)\b",
    re.I)

GAME_RE = re.compile(
    r"\b(nintendo|nes\b|snes|n64|gamecube|game\s*boy|gameboy|"
    r"sega|genesis|dreamcast|saturn|playstation|ps[1-5]\b|psp|xbox|"
    r"atari|cartridge|cib\b|wata|vga)\b", re.I)

CARD_RE = re.compile(
    r"\b(psa|bgs|sgc|cgc|bvg|topps|bowman|panini|upper\s*deck|fleer|"
    r"donruss|leaf|score|pinnacle|refractor|prizm|rookie\s*card|\brc\b|"
    # A Rookie Patch Auto names neither its maker nor, often, a grader:
    # "2019 IMMACULATE #132 PJ WASHINGTON JR TRUE RPA ROOKIE PATCH AUTO /99"
    # matched nothing here and came out object_class "unknown".
    r"patch\s*auto|auto\s*patch|\brpa\b|rookie\s*patch|"
    r"card\s*#|holo|carddass|topsun)\b", re.I)

# CGC and SGC grade comics as well as cards, so the graders cannot be used
# to tell the two apart. "Action Comics #22 1940 DC Comics CGC 4.0" was
# classified as a CARD purely because CGC appears in CARD_RE - and was then
# rejected against PriceCharting's own genre of "Comic Book".
CARD_ONLY_RE = re.compile(
    r"\b(topps|bowman|panini|upper\s*deck|fleer|donruss|leaf|pinnacle|"
    r"refractor|prizm|rookie\s*card|\brc\b|card\s*#|holo|carddass|"
    # 2026-08-02: a Rookie Patch Auto is a CARD, but its title is nothing
    # but memorabilia nouns and it rarely names its manufacturer, so
    # "National Treasures ... Patch Auto Jersey PSA 9" was filed as Sports
    # Memorabilia. That quietly emptied the Sports Cards tab of exactly the
    # modern sports product the watchlist targets most.
    r"patch\s*auto|auto\s*patch|\brpa\b|rookie\s*patch|"
    r"topsun)\b", re.I)

# Memorabilia nouns split by whether a CARD can legitimately carry them.
#
# "Jersey", "patch", "bat" and "ball" are all over relic cards - a 2022
# National Treasures Rookie Patch Auto is a card with a jersey swatch in it.
# "Photo", "flag", "canvas" and "poster" are not: no card is a canvas.
#
# Only the second group may outrank a brand name. Letting the first group do
# it stripped 68 genuine jersey/relic cards out of Sports Cards when this was
# first attempted, which is the opposite of the problem being fixed.
# NB: "patch" is deliberately absent. Adding it to this list swept in every
# National Treasures / The Cup "Rookie Patch Auto" - 178 real cards in one
# pass - because a patch card's title is nothing but memorabilia nouns.
SWATCH_NOUNS = (r"shirt|jersey|cap|hat|glove|helmet|bat\b|ball\b|puck|"
                r"club|driver|ticket")
NEVER_A_CARD = (r"photo|photograph|flag|pennant|banner|poster|print|plaque|"
                r"lithograph|canvas|program|magazine|"
                r"cut\s*signature|display\s*case|(video\s*)?game\s*cover|"
                r"blow[-\s]*up\s*card|custom\s*framed|"
                r"tourney\s*worn|tournament\s*worn|game\s*worn|match\s*worn")

# 2026-08-02: "Upper Deck" is in CARD_ONLY_RE because Upper Deck makes
# cards - but Upper Deck Authenticated is their signed-MEMORABILIA arm. A
# brand name therefore proved cardness for a signed photo, a signed
# video-game cover, a Masters flag and a signed canvas, and four such Tiger
# Woods items were most of what the Sports Cards tab contained.
STRONG_MEMORABILIA_RE = re.compile(rf"\b({NEVER_A_CARD})\b", re.I)

# The narrow set of card vocabulary allowed to overrule even those, because
# it names a card product rather than a manufacturer.
CARD_PRODUCT_RE = re.compile(
    r"\b(refractor|prizm|rookie\s*card|\brc\b|card\s*#|carddass|topsun|"
    r"patch\s*auto|auto\s*patch|\brpa\b|photo\s*variation|photo\s*var\b|"
    r"short\s*print|parallel|insert|die[-\s]*cut)\b", re.I)

# Video-game graders must not turn a sealed game into a "card" just because
# the title says graded.  Card graders are the card signal.
CARD_GRADERS = {"psa", "bgs", "sgc", "cgc", "bvg"}
GAME_GRADERS = {"wata", "vga"}

# Signed/worn/framed physical items that are NOT cards. Without this class,
# "Tiger Woods Framed Photo UDA" and "Tourney Worn Shirt" were object_class
# "unknown", conflicted with nothing, and sat happily in the ask pool for a
# rookie CARD - one of the ways 149 mongrel asks became a single $1,799.10.
MEMORABILIA_RE = re.compile(rf"\b({NEVER_A_CARD}|{SWATCH_NOUNS})\b", re.I)

OBJECT_CLASSES = ("watch_part", "watch", "figure", "wrapper", "pack",
                  "coupon", "comic", "game", "memorabilia", "card", "unknown")


def object_class(text: str) -> str:
    """Coarse physical class of the thing being sold.

    A complete watch and a watch dial are not the same market.  Neither are
    a trading card, the wax wrapper it came in, and a modern plastic figure
    of the same character.  Valuing across those boundaries is what produced
    the $2,821 McFarlane figure.
    """
    t = text or ""
    if WATCH_PART_RE.search(t):
        return "watch_part"
    if WATCH_RE.search(t):
        return "watch"
    if FIGURE_RE.search(t):
        return "figure"
    if WRAPPER_RE.search(t):
        return "wrapper"
    if COUPON_RE.search(t):
        return "coupon"
    if PACK_RE.search(t):
        return "pack"
    # Authentication companies also encapsulate/autograph-authenticate
    # photos, jerseys and cuts. ``PSA/DNA signed photo`` must not become a
    # card merely because the title contains PSA. Real jersey/relic cards
    # retain explicit card-product vocabulary and continue below.
    # A noun no card can carry - photo, flag, canvas, game cover - outranks
    # a brand name. Only card-PRODUCT vocabulary overrules it.
    if STRONG_MEMORABILIA_RE.search(t) and not CARD_PRODUCT_RE.search(t):
        return "memorabilia"
    if MEMORABILIA_RE.search(t) and not CARD_ONLY_RE.search(t):
        return "memorabilia"
    grader = (grader_of(t) or "").lower()
    if grader in CARD_GRADERS:
        # A card grader on a comic still means a comic (CGC grades both), so
        # the grader itself cannot be the card signal here.
        if COMIC_RE.search(t) and not CARD_ONLY_RE.search(t):
            return "comic"
        return "card"
    if grader in GAME_GRADERS:
        return "game"
    if COMIC_RE.search(t):
        return "comic"
    if GAME_RE.search(t):
        return "game"
    # A signed photo is not a rookie card. Card vocabulary wins, so a jersey
    # CARD ("2003 UD Authentics Jersey Card #12") stays a card while the
    # jersey itself does not.
    if MEMORABILIA_RE.search(t) and not CARD_RE.search(t):
        return "memorabilia"
    if CARD_RE.search(t):
        return "card"
    return "unknown"


# --------------------------------------------------------------------------
# Parallel / finish
# --------------------------------------------------------------------------
# Modern chrome/prizm products separate almost all of their value by parallel.
# A /10 Black and a /399 Pink of the same card are different assets with
# different markets.  The vocabulary below is intentionally explicit: an
# unrecognised word yields no parallel rather than a wrong one.

FINISH_WORDS = {
    "refractor", "xfractor", "superfractor", "prizm",
    "foil", "wave", "shimmer", "mojo", "disco", "laser", "lazer", "scope",
    "pulsar", "velocity", "hyper", "optic", "mosaic", "atomic",
    "cracked", "rainbow", "diamond", "sparkle", "reactive", "speckle",
    "camo", "snakeskin", "kaleidoscope",
}
# holo / reverse holo are NOT parallels here. `comps._holo` already models
# holo vs non-holo vs reverse as a VARIANT, and on vintage Pokemon the holo
# finish is intrinsic to the card rather than a separate print run. Treating
# it as a parallel made a Base Set Charizard score 82% on `Charizard [Holo]
# #4 | Pokemon Chinese CSM2cC` while the correct `Charizard #4 | Pokemon
# Base Set` was pushed out of the top three.
# Deliberately NOT finish words: tiger, zebra, flash, genesis, aurora,
# nebula, cosmic, galactic, ice, sapphire, marble. Each collides with a
# player name, a console or a set name - a live run on 2026-07-26 turned
# "Tiger Woods Red Tiger" into the parallel "red tiger" and spent a paid
# PriceCharting call on it. A niche finish is not worth a false identity.

COLOR_WORDS = {
    "black", "gold", "silver", "bronze", "copper", "red", "blue", "green",
    "orange", "purple", "pink", "teal", "aqua", "yellow", "white", "grey",
    "gray", "neon", "platinum", "ruby", "emerald", "sapphire", "amethyst",
    "onyx", "magenta", "violet", "turquoise", "lime",
}

PARALLEL_RE = re.compile(
    r"\b(" + "|".join(sorted(FINISH_WORDS | COLOR_WORDS)) + r")\b", re.I)

SERIAL_RE = re.compile(r"(?:#\s*/|/)\s*(\d{1,5})\b")
ONE_OF_ONE_RE = re.compile(r"\b(?:1\s*/\s*1|one\s+of\s+one)\b", re.I)

AUTO_RE = re.compile(
    r"\b(auto|autos|autograph(?:ed)?|signed|signature|on[-\s]*card\s*auto)\b",
    re.I)
RELIC_RE = re.compile(
    r"\b(relic|patch|jersey|memorabilia|game[-\s]*used|swatch|"
    r"bat\s*barrel|letterman)\b", re.I)

# "PSA Authentic" / "Authentic Altered" are NOT a numeric grade.  Treating
# them as ungraded (PSA-5-equivalent) is how five Mikan rows - authentic,
# altered and raw - collapsed onto one number.
QUALIFIER_RE = re.compile(
    r"\b(authentic\s*altered|auth\s*altered|altered|authentic|\bauth\b|"
    r"qualified|\bmk\b|miscut|trimmed|evidence\s*of\s*trimming)\b", re.I)


def parallel_of(text: str) -> str | None:
    """Normalised parallel/finish descriptor, or None when unrecognised.

    A bare colour is NOT a parallel. "Red" in a title is usually a jersey,
    a team or a nickname; it only names a parallel when a finish word or a
    print run is present too ("Pink Refractor", "Black #/10", "Gold Wave").
    Requiring that corroboration is what stops ordinary vintage listings
    from acquiring invented parallels.
    """
    words = [m.group(1).lower() for m in PARALLEL_RE.finditer(text or "")]
    if not words:
        return None
    if not (set(words) & FINISH_WORDS) and SERIAL_RE.search(text or "") is None:
        return None
    # "cracked ice" and "laser"/"lazer" normalise; order is dropped so
    # "Wave Orange" and "Orange Wave" are one parallel.
    norm = {"lazer": "laser", "gray": "grey", "holofoil": "holo"}
    seen: list[str] = []
    for w in words:
        w = norm.get(w, w)
        if w not in seen:
            seen.append(w)
    return " ".join(sorted(seen))


def serial_of(text: str) -> int | None:
    """Print-run denominator: '#/399' -> 399, '1/1' -> 1."""
    t = text or ""
    if ONE_OF_ONE_RE.search(t):
        return 1
    set_number = card_number(t)
    best = None
    for m in SERIAL_RE.finditer(t):
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            continue
        # A TCG card number like "6/102" is a set position, not a print run.
        if (set_number and re.search(
                rf"\b{re.escape(set_number)}\s*/\s*{n}\b", t, re.I)):
            continue
        # Print runs in this hobby are <= 5000; set sizes usually exceed the
        # numerator.  Keep the smallest plausible run.
        if 1 <= n <= 5000 and (best is None or n < best):
            best = n
    return best


# PriceCharting's `console-name` holds the SET ("2023 Topps Chrome Disney
# 100", "Pokemon Base Set", "1948 Bowman"). `subject_candidates` deliberately
# strips brand vocabulary as generic, which is right for naming the PLAYER
# but leaves us unable to tell one set from another - the reason a Base Set
# Charizard matched a Chinese promo set. These tokens are kept separately.
SET_WORDS = {
    "topps", "bowman", "panini", "fleer", "donruss", "upper", "deck", "leaf",
    "score", "pinnacle", "goudey", "playball", "t206", "t205", "chrome",
    "prizm", "optic", "select", "mosaic", "finest", "stadium", "club",
    "heritage", "allen", "ginter", "gypsy", "queen", "sapphire", "sterling",
    "immaculate", "flawless", "contenders", "exquisite", "sp", "spx",
    "metal", "ultra", "sportflics", "starting", "lineup", "star",
    # 2026-08-02: this list knew MANUFACTURERS but not PRODUCT LINES, and
    # the line is what separates two cards of the same player in the same
    # year. "Barry Bonds 1986 Topps Tiffany" scored `topps` only, so the
    # Tiffany and the base Topps candidates tied at 70% and the margin gate
    # refused a card that was perfectly identifiable - the one word that
    # told them apart was dropped before scoring. Same for Panini Noir,
    # The Cup, National Treasures, Bowman's Best and OPC.
    "tiffany", "noir", "cup", "national", "treasures", "best", "opc",
    "o-pee-chee", "parkhurst", "sporting", "news", "autographics",
    "scoreboard", "sott", "artifacts", "ultimate", "spectrum",
    "definitive", "obsidian", "spectra", "origins", "impeccable",
    "certified", "absolute", "prestige", "elite", "limited", "playoff",
    "revolution", "illusions", "phoenix", "encased", "clearly",
    "immaculate", "triple", "quad", "threads", "rookies", "signatures",
    "refractor", "superfractor", "atomic", "reactive", "bowman's",
    # Pokemon and TCG sets
    "base", "jungle", "fossil", "rocket", "gym", "neo", "expedition",
    "aquapolis", "skyridge", "topsun", "carddass", "vending", "promo",
    "shadowless", "evolutions", "celebrations", "crown", "zenith",
    "pokemon", "disney", "marvel", "wwe", "ufc", "garbage", "pail",
    # Video-game platforms. PriceCharting's `console-name` IS the platform
    # ("NES", "PAL NES", "Game & Watch"), and telling those apart is the
    # whole ballgame: the same title on NES and Game & Watch are different
    # products. "nes" is in comps.GENERIC_TOKENS so subject extraction drops
    # it, which left game rows with no console signal at all.
    "nes", "snes", "n64", "gamecube", "gameboy", "gba", "gbc", "ds", "3ds",
    "wii", "switch", "genesis", "megadrive", "dreamcast", "saturn",
    "playstation", "psp", "vita", "xbox", "atari", "intellivision",
    "colecovision", "turbografx", "neogeo", "jaguar", "3do",
}

# Words a game title needs that generic-token stripping would remove.
# "Super Mario Bros" and "Mario Bros" are different games; losing "Super"
# turned a 43% correct match into a 46% wrong one.
# "version" and "game" are NOT stripped: "Pokemon Red Version" is the actual
# product name, and dropping "Game" from "Game Boy" left us searching for
# 'pokemon red boy'.
GAME_STOPWORDS = {"sealed", "graded", "wata", "vga", "cgc", "cib",
                  "complete", "boxed", "loose", "factory", "shrinkwrap",
                  "shrinkwrapped", "mint", "authentic", "console", "copy"}


def join_platforms(text: str) -> str:
    """Collapse two-word platform names to the catalogue's single word.

    We say "Game Boy"; PriceCharting says "GameBoy". Normalising only one
    side meant a sealed Pokemon Red scored 50% recall against its own
    product, because our 'game' and 'boy' never matched their 'gameboy'.
    Both the query and the candidate must be folded the same way.
    """
    text = re.sub(r"\bgame\s*boy\b", "gameboy", text or "", flags=re.I)
    text = re.sub(r"\bmega\s*drive\b", "megadrive", text, flags=re.I)
    text = re.sub(r"\bneo\s*geo\b", "neogeo", text, flags=re.I)
    text = re.sub(r"\bsuper\s*nintendo\b", "snes", text, flags=re.I)
    return text


def set_tokens_of(text: str) -> tuple:
    """Brand/set words present in a title, in title order, de-duplicated."""
    out: list[str] = []
    for word in re.findall(r"[A-Za-z]+", join_platforms(text)):
        w = word.casefold()
        if w in SET_WORDS and w not in out:
            out.append(w)
    return tuple(out[:4])


# comps.card_number only reads pure digits, which is right for the comp
# filter it was built for. Modern inserts and parallels number themselves
# alphanumerically - #T264, #IM-15, #BC-15, #RA-MJ - and those returned None,
# so the very cards whose identity matters most had no card number at all.
# Kept local to identity so comp-filtering semantics are untouched.
ALNUM_NUMBER_RE = re.compile(
    r"#\s*([A-Za-z]{1,4}-?\d{1,4}[A-Za-z]?|\d{1,4}[A-Za-z])(?![\d/])")


# Regional releases are separate products with separate markets.
REGION_RE = re.compile(
    r"\b(pal|famicom|jp\s*import|japan\s*import|ntsc-?j|asian?\s*version)\b",
    re.I)

BRACKET_RE = re.compile(r"\[([^\]]+)\]")


def catalogue_parallel(product_name: str) -> str | None:
    """The parallel a PriceCharting product name declares in brackets.

    PriceCharting writes the parallel as `Cinderella [Pink] #45`. The bracket
    IS the declaration, so a bare colour inside it needs no corroboration -
    unlike a colour floating in an eBay title, where "red" is usually a
    jersey. Without this, `[Pink]` parsed to no parallel at all and lost its
    colour match, letting the generic `Cinderella [Refractor]` outrank the
    correct `Cinderella [Pink]` 57% to 54%.
    """
    m = BRACKET_RE.search(product_name or "")
    if not m:
        return None
    words = [w.casefold() for w in re.findall(r"[A-Za-z]+", m.group(1))]
    norm = {"lazer": "laser", "gray": "grey", "holofoil": "holo"}
    keep = [norm.get(w, w) for w in words
            if w in FINISH_WORDS or w in COLOR_WORDS]
    return " ".join(sorted(set(keep))) if keep else None


def card_number_of(text: str) -> str | None:
    """Card number including alphanumeric insert numbering."""
    plain = card_number(text or "")
    if plain:
        return plain
    m = ALNUM_NUMBER_RE.search(text or "")
    if not m:
        return None
    value = m.group(1).upper()
    if re.fullmatch(r"(1[89]\d{2}|20\d{2})", value):
        return None                     # a year, not a card number
    return value


def _parallel_overlap(a: str | None, b: str | None) -> float | None:
    """0..1 word overlap between two parallel descriptors, None if unknown.

    "orange refractor wave" vs "orange wave" -> 0.67, i.e. the same card.
    Equality comparison scored that pair as a MISMATCH and penalised the
    correct product down to 22%.
    """
    if not a or not b:
        return None
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return None
    return len(ta & tb) / max(len(ta), len(tb))


def qualifier_of(text: str) -> str | None:
    m = QUALIFIER_RE.search(text or "")
    if not m:
        return None
    word = re.sub(r"\s+", " ", m.group(1).strip().lower())
    if "altered" in word:
        return "altered"
    if word in ("auth", "authentic"):
        return "authentic"
    if word in ("mk", "qualified"):
        return "qualified"
    return word


# --------------------------------------------------------------------------
# The identity itself
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CardIdentity:
    object_class: str = "unknown"
    year: str | None = None
    subject: tuple = ()
    number: str | None = None
    parallel: str | None = None
    serial: int | None = None
    is_auto: bool = False
    is_relic: bool = False
    grader: str | None = None
    grade: float | None = None          # PSA-equivalent, Andrew's shift
    printed_grade: float | None = None  # as it appears on the slab
    qualifier: str | None = None
    variant: tuple = ()                 # holo / edition / series / completeness
    set_tokens: tuple = ()              # brand/set words -> PriceCharting console
    source_text: str = field(default="", compare=False, repr=False)

    # -- construction ------------------------------------------------------
    @classmethod
    def from_text(cls, text: str, *, subject_tokens: int = 2) -> "CardIdentity":
        t = text or ""
        gi = grade_info(t)
        grader = grader_of(t)
        qualifier = qualifier_of(t)
        printed = float(gi[1]) if gi and _num(gi[1]) is not None else None
        eff = float(gi[2]) if gi and _num(gi[2]) is not None else None
        if qualifier in ("authentic", "altered"):
            # An authentic/altered slab has no numeric grade to shift.
            printed, eff = None, None
        yrs = sorted(years_in(t))
        variant = tuple(v for v in (
            comps_mod._holo(t), comps_mod._edition(t),
            comps_mod._topsun_series(t), comps_mod._completeness(t),
        ) if v)
        return cls(
            object_class=object_class(t),
            year=yrs[0] if yrs else None,
            subject=tuple(subject_candidates(t, limit=subject_tokens)),
            number=card_number_of(t),
            parallel=parallel_of(t),
            serial=serial_of(t),
            is_auto=bool(AUTO_RE.search(t)),
            is_relic=bool(RELIC_RE.search(t)),
            grader=grader,
            grade=eff,
            printed_grade=printed,
            qualifier=qualifier,
            variant=variant,
            set_tokens=set_tokens_of(t),
            source_text=t,
        )

    # -- keys and scores ---------------------------------------------------
    def fingerprint(self) -> str:
        """Stable grouping key. Same fingerprint == same tradeable asset."""
        grade = ("auth:" + self.qualifier) if self.qualifier else (
            f"{self.grade:g}" if self.grade is not None else "raw")
        return "|".join([
            self.object_class,
            self.year or "?",
            " ".join(self.subject) or "?",
            self.number or "?",
            self.parallel or "-",
            str(self.serial or "-"),
            "auto" if self.is_auto else "-",
            "relic" if self.is_relic else "-",
            " ".join(self.variant) or "-",
            grade,
        ])

    def specificity(self) -> float:
        """0..1 - how precisely this listing names a single physical asset.

        Used to decide whether a row may become a bid target. What counts as
        "pinned" depends on WHAT the thing is, which the first version got
        wrong: it demanded a card number and a parallel, so every sealed
        video game scored 50% and was permanently locked out of guide-led
        valuation - despite video games being PriceCharting's deepest and
        oldest catalogue. A sealed WATA-graded Super Mario Bros on NES is
        completely identified and has no card number to give.
        """
        score = 0.0
        if self.object_class != "unknown":
            score += 0.15
        if self.subject:
            score += 0.20
        if self.year:
            score += 0.10
        graded = self.grade is not None or self.qualifier

        if self.object_class == "game":
            # title + platform + condition/grade IS a specific game.
            if self.set_tokens or self.variant:
                score += 0.25          # platform / sealed-vs-loose state
            if graded:
                score += 0.25
            if self.number:
                score += 0.05
        elif self.object_class == "comic":
            # title + issue number + year is a specific book.
            if self.number:
                score += 0.30
            if graded:
                score += 0.20
        else:
            if self.number:
                score += 0.25
            if graded:
                score += 0.15
            # A serialised/parallel card that names its parallel is fully
            # pinned; one that doesn't is ambiguous among its own siblings.
            if self.serial is not None or self.parallel:
                score += 0.15
            elif self.variant:
                # "Green Back", "1st Edition", "Shadowless" are real
                # discriminators on vintage cards that carry no parallel.
                score += 0.10
        return round(min(1.0, score), 3)

    def is_specific(self, floor: float = 0.70) -> bool:
        return self.specificity() >= floor

    # -- comparison --------------------------------------------------------
    def conflicts_with(self, other: "CardIdentity") -> str | None:
        """Why these are different assets, in words - or None if compatible.

        Only fields that BOTH sides state can conflict.  An unknown field is
        never treated as agreement; it simply cannot rule the pair out, which
        is why `specificity()` gates action separately.
        """
        if (self.object_class != "unknown" and other.object_class != "unknown"
                and self.object_class != other.object_class):
            return f"{self.object_class} vs {other.object_class}"
        if self.year and other.year and self.year != other.year:
            return f"year {self.year} vs {other.year}"
        if self.number and other.number and self.number != other.number:
            return f"card #{self.number} vs #{other.number}"
        if (self.parallel and other.parallel
                and self.parallel != other.parallel):
            return f"parallel {self.parallel!r} vs {other.parallel!r}"
        if (self.serial is not None and other.serial is not None
                and self.serial != other.serial):
            return f"serial /{self.serial} vs /{other.serial}"
        if self.is_auto != other.is_auto:
            return "autograph mismatch"
        if self.is_relic != other.is_relic:
            return "relic/memorabilia mismatch"
        if self.qualifier != other.qualifier:
            return (f"grade qualifier {self.qualifier or 'none'} vs "
                    f"{other.qualifier or 'none'}")
        if self.subject and other.subject and not (
                set(self.subject) & set(other.subject)):
            return (f"subject {' '.join(self.subject)!r} vs "
                    f"{' '.join(other.subject)!r}")
        sv, ov = set(self.variant), set(other.variant)
        clash = {a for a in sv for b in ov
                 if a.split(":")[0] == b.split(":")[0] and a != b}
        if clash:
            return f"variant {sorted(sv)} vs {sorted(ov)}"
        return None

    def matches(self, other: "CardIdentity") -> bool:
        return self.conflicts_with(other) is None

    def discriminates(self, chosen: "CardIdentity",
                      other: "CardIdentity") -> bool:
        """True when THIS listing positively tells `chosen` apart from `other`.

        The margin gate exists to catch ties dressed up as convictions. But a
        runner-up we have ALREADY ruled out on stated evidence is not a tie -
        it is a candidate we correctly rejected, and letting it suppress the
        winner throws away a real identification.

        Real case: a "1st Edition Base Set Charizard" listing ranked
        `Charizard [1st Edition] #4 | Pokemon Base Set` first at 95%, with
        the unlimited `Charizard #4` from the same set 5% behind. The listing
        states 1st Edition and the winner matches it; the runner-up does not.
        That is discrimination, not ambiguity.
        """
        # edition / holo / shadowless: stated by us, present on the winner,
        # absent from the loser
        mine, won, lost = (set(self.variant), set(chosen.variant),
                           set(other.variant))
        if mine and (mine & won) and not (mine & lost):
            return True
        my_c = set((self.parallel or "").split()) & COLOR_WORDS
        won_c = set((chosen.parallel or "").split()) & COLOR_WORDS
        lost_c = set((other.parallel or "").split()) & COLOR_WORDS
        if my_c and (my_c & won_c) and not (my_c & lost_c):
            return True
        if self.subject:
            mine_s = set(self.subject)
            if (mine_s & set(chosen.subject)) and not (
                    mine_s & set(other.subject)):
                return True     # we named the character; the loser is another
        if (self.number and chosen.number == self.number
                and other.number != self.number):
            return True
        if (self.serial is not None and chosen.serial == self.serial
                and other.serial != self.serial):
            return True
        return False

    # -- PriceCharting ------------------------------------------------------
    def guide_query(self) -> str:
        """Search phrase for PriceCharting /api/products.

        Grade is excluded on purpose: one PriceCharting product carries every
        grade rung, so the product search must be grade-free.  Parallel and
        serial ARE included, because PriceCharting catalogues parallels as
        separate products and that is exactly the distinction we were losing.
        """
        if self.object_class == "game":
            # A game's TITLE is the product name, so use it rather than two
            # extracted subject tokens. Subject extraction is tuned for
            # cards, where the player is the subject and everything else is
            # context; on "Super Mario Bros NES Sealed WATA 9.4" it dropped
            # "Super" as a generic adjective and searched for "mario bros".
            words = [w for w in re.findall(r"[A-Za-z0-9]+",
                                           join_platforms(self.source_text))
                     if w.casefold() not in GAME_STOPWORDS
                     and not re.fullmatch(r"\d+(\.\d+)?", w)
                     and len(w) > 1]
            seen, out = set(), []
            for w in words:
                k = w.casefold()
                if k not in seen:
                    seen.add(k)
                    out.append(k)
            return " ".join(out[:8])

        parts: list[str] = []
        if self.year:
            parts.append(self.year)
        # Set words go in: PriceCharting matches on title AND console, and
        # without them '1948 george mikan #69' returned only comic books.
        parts.extend(self.set_tokens)
        parts.extend(self.subject)
        if self.number:
            parts.append(f"#{self.number}")
        if self.parallel:
            parts.append(self.parallel)
        if self.serial is not None and self.serial > 1:
            parts.append(f"/{self.serial}")
        if self.is_auto:
            parts.append("auto")
        seen, out = set(), []
        for p in parts:
            k = p.casefold()
            if k and k not in seen:
                seen.add(k)
                out.append(p)
        return " ".join(out)

    def score_candidate(self, product_name: str,
                        console_name: str = "") -> float:
        """0..1 confidence that a PriceCharting product IS this asset.

        PriceCharting splits identity across two fields and they mean
        different things, so they are scored separately:

          product-name -> the CARD  ("Zurg [Orange Wave] #28")
          console-name -> the SET   ("2023 Topps Chrome Disney 100")

        Scoring them as one blob is how a Base Set Charizard once matched
        `Pokemon Chinese CSM2cC` at 73% while the correct `Pokemon Base Set`
        product scored 67% - the right answer losing to the wrong one.
        """
        name = (product_name or "").strip()
        console = (console_name or "").strip()
        if not name and not console:
            return 0.0
        # identity_of, not from_text: a product name is parsed once per
        # SCAN this way instead of once per listing that scores against it.
        # 2026-08-08 profile: 11,958 from_text calls for 150 evaluations,
        # ~80 per listing, nearly all of them re-parsing the same catalogue
        # rows. CardIdentity is frozen, so sharing one is safe.
        other = identity_of(f"{name} {console}".strip())
        declared = catalogue_parallel(name)
        if declared:
            other = replace(other, parallel=declared)

        # A stated card-number or serial disagreement means a different card,
        # no matter how well the words line up.
        if self.number and other.number and self.number != other.number:
            return 0.0
        if (self.serial is not None and other.serial is not None
                and self.serial != other.serial):
            return 0.0
        if self.year and other.year and self.year != other.year:
            return 0.0
        # A different player is a different card, however well the numbering
        # lines up. SportsCardsPro returned `Chris Smith #T264` and `Mike
        # Adams #T264` at 64% each for an Ichiro listing: same set, same card
        # number, entirely the wrong person. Card number and set agreement
        # must never outvote the name on the card.
        if (self.object_class != "game" and self.subject and other.subject
                and not (set(self.subject) & set(other.subject))):
            return 0.0

        # --- the card ---------------------------------------------------
        want_name = " ".join(list(self.subject)
                             + ([f"#{self.number}"] if self.number else [])
                             + ([self.parallel] if self.parallel else []))
        score = 0.42 * title_match_score(want_name or self.guide_query(), name)
        if self.number and other.number == self.number:
            score += 0.18

        # Parallel names are descriptive, not canonical: a listing saying
        # "Orange Wave Refractor" and a catalogue saying "[Orange Wave]" are
        # the same card. Compare by overlap, never by equality.
        #
        # Colour carries far more identity than finish. Nearly every card in
        # a modern set is *some* kind of refractor, so "refractor" barely
        # narrows anything, while "pink" picks out one parallel. Weighting
        # them equally let `Cinderella [Refractor]` outrank the correct
        # `Cinderella [Pink]` for a listing that said "Pink Refractor".
        mine = set((self.parallel or "").split())
        theirs = set((other.parallel or "").split())
        my_colours, their_colours = mine & COLOR_WORDS, theirs & COLOR_WORDS
        if my_colours and their_colours:
            if my_colours & their_colours:
                score += 0.16       # same colour: the strong signal
            else:
                score -= 0.22       # different colours: a different card
        overlap = _parallel_overlap(self.parallel, other.parallel)
        if overlap is not None:
            if overlap >= 0.5:
                score += 0.08 * overlap
            elif overlap == 0.0:
                score -= 0.12
        elif self.parallel and not other.parallel:
            score += 0.02           # catalogue simply omits the finish
        elif other.parallel and not self.parallel:
            score -= 0.08           # base card matched to a parallel product

        # A bracketed qualifier we never asked for is a variant, and a base
        # card is not its variant. SportsCardsPro returned `Michael Jordan
        # #57` at 90% and `Michael Jordan [20th Anniversary] #57` at 86% -
        # four points apart, which the margin gate reads as a coin flip even
        # though the listing says nothing about an anniversary reprint.
        bracket = BRACKET_RE.search(name)
        if bracket:
            mine_low = (self.source_text or "").casefold()
            unasked = [w for w in re.findall(r"[A-Za-z]{3,}", bracket.group(1))
                       if w.casefold() not in mine_low]
            if unasked:
                score -= min(0.18, 0.07 * len(unasked))

        if self.serial is not None and other.serial == self.serial:
            score += 0.06
        if self.is_auto == other.is_auto:
            score += 0.04

        # 1st Edition / Shadowless / Unlimited are different cards with very
        # different money, and PriceCharting catalogues them as separate
        # products ("Charizard [1st Edition] #4"). Without this the correct
        # edition tied with its siblings and the margin gate refused a card
        # we could actually have identified.
        mine_v, theirs_v = set(self.variant), set(other.variant)
        if mine_v and theirs_v:
            if mine_v & theirs_v:
                score += 0.10
            else:
                score -= 0.08

        # A foreign-language printing is a different card with a different
        # market. `Pokemon Korean Base Set` tied with `Pokemon Base Set` at
        # 95% because both contain the set tokens we search on. PAL/Famicom
        # are the same idea for games: a different region is a different
        # product with a different price.
        cand_text = f"{name} {console}"
        mine_text = self.source_text or ""
        if (bool(comps_mod.FOREIGN_RE.search(cand_text))
                != bool(comps_mod.FOREIGN_RE.search(mine_text))):
            score -= 0.30
        if (bool(REGION_RE.search(cand_text))
                != bool(REGION_RE.search(mine_text))):
            score -= 0.25

        # Games are identified by their TITLE, so every meaningful word of
        # ours has to appear. Without this "Mario Bros" outranked "Super
        # Mario Bros" 72% to 69% - a different, far cheaper game.
        if self.object_class == "game":
            mine_w = {w for w in re.findall(r"[a-z0-9]+", self.guide_query())
                      if w not in SET_WORDS}
            cand_w = set(re.findall(r"[a-z0-9]+",
                                    join_platforms(cand_text).casefold()))
            if mine_w:
                recall = len(mine_w & cand_w) / len(mine_w)
                score += 0.22 * recall - 0.11
                # ...and words we did NOT ask for count against it. A
                # listing that says "Super Mario Bros" is not "Super Mario
                # Bros 3" or "Super Mario Bros and Duck Hunt", which
                # otherwise tied with it to within 1%.
                # Single characters are kept when they are digits: the whole
                # difference between "Super Mario Bros" and "Super Mario
                # Bros 3" is one character, and it is worth thousands.
                extra = {w for w in cand_w - mine_w
                         if w not in SET_WORDS and w not in GAME_STOPWORDS
                         and (len(w) > 1 or w.isdigit())}
                score -= min(0.24, 0.09 * len(extra))

        # --- the set ----------------------------------------------------
        # Set membership is decided by CONTAINMENT, not fuzzy similarity. A
        # Base Set Charizard matched `Pokemon Chinese CSM2cC` at 78% while
        # the correct `Pokemon Base Set` scored 63%, because "Charizard
        # [Holo] #4" beat "Charizard #4" on the card name alone. The set the
        # card actually belongs to has to be able to outvote that.
        if console and self.set_tokens:
            low = console.casefold()
            hit = sum(1 for w in self.set_tokens if w in low)
            score += 0.26 * (hit / len(self.set_tokens))
            if hit == 0:
                score -= 0.12       # named a set, landed in a different one
        elif console and self.year and self.year in console:
            score += 0.08
        return round(max(0.0, min(1.0, score)), 3)


# Match-quality bands used by the guide and the engine.
MATCH_EXACT = "exact"        # this product IS the card
MATCH_STRONG = "strong"      # very likely the card
MATCH_WEAK = "weak"          # same family, not pinned
MATCH_NONE = "none"

EXACT_FLOOR = 0.80
STRONG_FLOOR = 0.62
WEAK_FLOOR = 0.40


def match_band(score: float) -> str:
    if score >= EXACT_FLOOR:
        return MATCH_EXACT
    if score >= STRONG_FLOOR:
        return MATCH_STRONG
    if score >= WEAK_FLOOR:
        return MATCH_WEAK
    return MATCH_NONE


@lru_cache(maxsize=200_000)
def identity_of(text: str) -> CardIdentity:
    """Memoised extraction.

    A full scan values ~4,000 listings against pools of up to 120 comps each,
    so naive extraction would parse half a million titles per run. Comp titles
    repeat heavily across listings, so the cache hit rate is very high.
    CardIdentity is frozen, which makes it safe to share.
    """
    return CardIdentity.from_text(text)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def genre_class(genre: str | None) -> str | None:
    """Map PriceCharting's own `genre` field onto our object classes.

    PriceCharting already tells us "Pokemon Card", "Comic", and so on. Using
    it is free corroboration that we resolved to the right KIND of thing.
    """
    if not genre:
        return None
    g = genre.casefold()
    if "card" in g:
        return "card"
    if "comic" in g:
        return "comic"
    if any(w in g for w in ("rpg", "action", "fighting", "shooter", "puzzle",
                            "platform", "racing", "sports", "strategy",
                            "adventure", "simulation", "party")):
        return "game"
    return None
