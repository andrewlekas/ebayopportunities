"""Local price-guide CSVs, used before ever touching the network.

WHY
---
PriceCharting's paid API is one call per second and we observe ~2s of round
trip, so a few hundred lookups cost ~20 minutes - the dominant cost of a full
scan. Their CSV download carries the SAME data for a whole set in a single
request, refreshed every 24 hours, and the column names are identical to the
API's JSON keys. So a CSV row can be handed to exactly the same grade-ladder
and candidate-scoring code with no translation at all.

Both pricecharting.com and sportscardspro.com offer this to Legendary
subscribers, which is the same tier that grants API access.

HOW TO FEED IT
--------------
Drop any number of `.csv` files into the `guide_csv/` folder beside
config.yaml. Get them from the "Download Price List" link on any set page,
or from Subscriptions -> API/Download. Filenames do not matter; the loader
reads the header row and ignores anything that is not a price guide.

Nothing here talks to the network. If a card is not in the local files the
guide falls back to the paid API exactly as before, so a partial set of CSVs
is genuinely useful - every set you add is a set that stops costing calls.
"""
from __future__ import annotations

import csv
import heapq
import glob
import logging
import os
import re

log = logging.getLogger(__name__)

# The columns we need. Anything else in the file is ignored, so extra
# retailer columns (buy/sell prices, UPC, GameStop) do no harm.
REQUIRED = ("product-name", "console-name")
PRICE_FIELDS = (
    "loose-price", "cib-price", "new-price", "graded-price",
    "box-only-price", "manual-only-price", "bgs-10-price",
    "condition-17-price", "condition-18-price",
)
CARRY = PRICE_FIELDS + ("id", "genre", "sales-volume", "release-date")

# A row's searchable tokens. Kept deliberately small and lowercase so the
# index stays cheap: a full guide can be hundreds of thousands of rows.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

MAX_ROWS = 2_000_000        # refuse to eat unbounded memory
MAX_CANDIDATES = 60         # per lookup, mirrors the API's page size


def _cents(value) -> int | None:
    """CSV prices arrive as '$1,234.56' or as raw pennies. Normalise."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("$") or "." in text:
        try:
            return int(round(float(text.replace("$", "").replace(",", "")) * 100))
        except ValueError:
            return None
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


class GuideCsvIndex:
    """An in-memory, token-indexed view of every local price-guide CSV."""

    def __init__(self, folder: str):
        self.folder = folder
        self.rows: list[dict] = []
        self._index: dict[str, list[int]] = {}
        self.files: list[str] = []
        self._common_at = 2000        # replaced in load(); safe if unloaded
        self._pool_cap = 6000         # most candidates worth scoring

    # -- loading -------------------------------------------------------
    def load(self) -> "GuideCsvIndex":
        if not self.folder or not os.path.isdir(self.folder):
            return self
        for path in sorted(glob.glob(os.path.join(self.folder, "*.csv"))):
            try:
                self._load_file(path)
            except (OSError, csv.Error) as exc:
                log.warning("price CSV %s could not be read: %s",
                            os.path.basename(path), exc)
        # A token in more than this share of the catalogue cannot narrow
        # anything, so searching it is pure cost. 2% of 641k rows is ~12,800:
        # "charizard" (5k) survives, "pokemon" (92k) and "comic" (348k) do
        # not. Recomputed on every load because it scales with the files
        # you have downloaded.
        self._common_at = max(2000, int(len(self.rows) * 0.02))
        self._pool_cap = 6000
        if self.rows:
            log.info("price guide CSVs: %d rows from %d file(s) - these cost "
                     "no API calls", len(self.rows), len(self.files))
        return self

    def _load_file(self, path: str) -> None:
        basename = os.path.basename(path)
        guide_host = ("sportscardspro" if basename.casefold().startswith(
            "sportscardspro--") else
            "pricecharting" if basename.casefold().startswith(
                "pricecharting--") else "")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames or not all(
                    c in reader.fieldnames for c in REQUIRED):
                log.warning("price CSV %s has no product-name/console-name "
                            "header - skipping", os.path.basename(path))
                return
            added = 0
            for raw in reader:
                if len(self.rows) >= MAX_ROWS:
                    log.warning("price CSVs hit the %d-row ceiling - "
                                "ignoring the rest", MAX_ROWS)
                    break
                name = (raw.get("product-name") or "").strip()
                if not name:
                    continue
                row = {"product-name": name,
                       "console-name": (raw.get("console-name") or "").strip(),
                       "_guide-host": guide_host}
                for key in CARRY:
                    val = raw.get(key)
                    if val in (None, ""):
                        continue
                    row[key] = _cents(val) if key in PRICE_FIELDS else val
                self._add(row)
                added += 1
        if added:
            self.files.append(os.path.basename(path))

    def _add(self, row: dict) -> None:
        position = len(self.rows)
        self.rows.append(row)
        blob = f"{row['product-name']} {row['console-name']}".casefold()
        for token in set(_TOKEN_RE.findall(blob)):
            self._index.setdefault(token, []).append(position)

    # -- lookup --------------------------------------------------------
    def search(self, query: str, limit: int = MAX_CANDIDATES) -> list[dict]:
        """Rows sharing the most tokens with the query.

        This is a candidate SHORTLIST, not a decision. The caller scores the
        result with the same CardIdentity.score_candidate used on API
        responses, so local and remote answers are held to one standard.
        """
        if not self.rows:
            return []
        tokens = set(_TOKEN_RE.findall((query or "").casefold()))
        if not tokens:
            return []

        # SHORTLIST on the rarest tokens, then SCORE on all of them.
        #
        # 2026-08-08 profile: this was 63% of valuation at 48ms a call,
        # because a title containing "comic" walked 347,716 postings and one
        # containing "the" walked 86,073 - just to add every row to a tally
        # that then had to be sorted.
        #
        # Simply ignoring common tokens was tried first and was wrong: card
        # NUMBERS are common tokens ("2" is in 45,064 products) and they are
        # decisive, so "pokemon jungle electrode #2" started answering
        # Electrode #101. Only 67% of queries kept their top candidate.
        #
        # So the split is between finding candidates and ranking them. The
        # rarest token - "electrode", a few hundred rows - is enough to find
        # every plausible row. Ranking then counts ALL the query's tokens
        # against each candidate, exactly as before, so "#2" still decides
        # between Electrode #2 and Electrode #101.
        ranked = sorted(tokens, key=lambda t: len(self._index.get(t, ())))
        pool: list[int] = []
        seen: set[int] = set()
        for token in ranked:
            postings = self._index.get(token, ())
            if pool and (len(postings) > self._common_at
                         or len(pool) >= self._pool_cap):
                break
            for position in postings:
                if position not in seen:
                    seen.add(position)
                    pool.append(position)
        if not pool:
            return []

        hits: dict[int, int] = {}
        for position in pool:
            row = self.rows[position]
            blob = f"{row['product-name']} {row['console-name']}".casefold()
            hits[position] = len(tokens & set(_TOKEN_RE.findall(blob)))
        # nlargest beats a full sort when we want 60 out of thousands.
        best = heapq.nlargest(limit, hits.items(), key=lambda kv: kv[1])
        return [self.rows[position] for position, _ in best]

    def __len__(self) -> int:
        return len(self.rows)


# One parse per folder per process. A full guide is tens of MB, and every
# PriceGuide construction would otherwise re-read and re-index it - which
# showed up immediately as a test suite going from 1.6s to 29s. Keyed on the
# folder's contents (names + mtimes + sizes) so a freshly downloaded CSV is
# still picked up without a restart.
_CACHE: dict[tuple, GuideCsvIndex] = {}


def _fingerprint(folder: str) -> tuple:
    if not folder or not os.path.isdir(folder):
        return (folder,)
    stamps = []
    for path in sorted(glob.glob(os.path.join(folder, "*.csv"))):
        try:
            st = os.stat(path)
            stamps.append((os.path.basename(path), int(st.st_mtime),
                           st.st_size))
        except OSError:
            continue
    return (folder, tuple(stamps))


def load_index(base_dir: str, folder_name: str = "guide_csv") -> GuideCsvIndex:
    folder = os.path.join(base_dir or ".", folder_name)
    key = _fingerprint(folder)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    index = GuideCsvIndex(folder).load()
    if len(_CACHE) > 4:     # bounded; a scan only ever uses one folder
        _CACHE.clear()
    _CACHE[key] = index
    return index
