"""Custom Basket Pricer - price a list of up to 500 cards in one pass.

    .venv/bin/python code/basket_pricer.py --in baskets/my_cards.xlsx
    .venv/bin/python code/basket_pricer.py --seed-set "Pokemon Base Set" \
                                           --variant "1st Edition"

WHY THIS IS NOT THE SCANNER
---------------------------
The scanner reads free-text eBay titles written by strangers, so it must
refuse to guess: "Charizard #4" matches five different Base Set products
priced $370 to $3,099, and picking one would be a fabricated number. That
refusal is correct there and is the whole point of the margin gate.

Here YOU supply the card, so the ambiguity mostly disappears. Give a Set
and a Name and the match is exact and deterministic - no scoring, no
guessing. Free-text names still work, but they run through the SAME
identity resolution the scanner uses and are refused the same way when
they are genuinely ambiguous. An ambiguous row is reported with its
candidates; it is never silently resolved.

Pricing itself is not reimplemented. Grade routing calls the scanner's own
`_guide_cents`, so a PSA 8.5 interpolates between rungs here exactly as it
does in a scan, and a CGC 10 uses the published CGC-10 field rather than
inheriting PSA 10 money.

INPUT
-----
A spreadsheet (.xlsx or .csv) with a column of names and a column of
grades. Header names are matched loosely, so all of these work:

    Card / Name / Product / Item          <- required
    Grade / Condition                     <- optional, blank = ungraded
    Set / Console / Console-Name          <- optional but recommended

COST
----
Local CSVs answer everything they cover at zero API calls. Only rows the
CSVs miss reach the paid API, and never more than --api-cap of them
(default 50), so a 500-row basket cannot quietly burn a run's quota.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main as scanner                                      # noqa: E402
from valuation.comps import grade_info                      # noqa: E402
from valuation.guide_csv import load_index                  # noqa: E402
from valuation.identity import identity_of                  # noqa: E402
from valuation.price_guide import PriceGuide, _guide_cents  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASKET_DIR = os.path.join(ROOT, "baskets")
MAX_ROWS = 500

NAME_HEADERS = ("card", "name", "product", "product-name", "item", "title")
GRADE_HEADERS = ("grade", "condition", "slab")
SET_HEADERS = ("set", "console", "console-name", "series")


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm(text) -> str:
    """Comparison key: case, spacing and punctuation are not identity."""
    return " ".join(str(text or "").split()).casefold()


def _pick_column(headers: list[str], wanted: tuple[str, ...]) -> int | None:
    """Index of the first header matching one of `wanted`.

    Exact match first so a sheet with both "Card" and "Card Number" picks
    "Card", not whichever happened to be leftmost.
    """
    lowered = [_norm(h).replace("_", "-") for h in headers]
    for want in wanted:
        if want in lowered:
            return lowered.index(want)
    for i, h in enumerate(lowered):
        if any(w in h for w in wanted):
            return i
    return None


# ---------------------------------------------------------------- input ---
def read_basket(path: str) -> tuple[list[dict], list[str]]:
    """(rows, warnings). Each row: {name, grade, set, line}."""
    ext = os.path.splitext(path)[1].casefold()
    if ext in (".xlsx", ".xlsm"):
        table = _read_xlsx(path)
    else:
        with open(path, newline="", encoding="utf-8-sig",
                  errors="replace") as fh:
            table = [r for r in csv.reader(fh)]
    table = [r for r in table if any(str(c or "").strip() for c in r)]
    if not table:
        return [], ["the file is empty"]

    headers = [str(c or "") for c in table[0]]
    i_name = _pick_column(headers, NAME_HEADERS)
    i_grade = _pick_column(headers, GRADE_HEADERS)
    i_set = _pick_column(headers, SET_HEADERS)

    warnings = []
    body = table[1:]
    if i_name is None:
        # No recognisable header: treat column A as names rather than
        # failing outright, but say so - silently discarding a header row
        # would drop a card.
        warnings.append(
            f"no name column found among {headers[:6]} - using the first "
            "column, and treating row 1 as data")
        i_name, body = 0, table
    if i_grade is None:
        warnings.append("no grade column found - pricing everything ungraded")

    rows = []
    for n, raw in enumerate(body, start=2):
        def cell(i):
            return (str(raw[i]).strip()
                    if i is not None and i < len(raw) and raw[i] is not None
                    else "")
        name = cell(i_name)
        if not name:
            continue
        rows.append({"name": name, "grade": cell(i_grade),
                     "set": cell(i_set), "line": n})
    if len(rows) > MAX_ROWS:
        warnings.append(f"{len(rows)} rows supplied - pricing the first "
                        f"{MAX_ROWS} and ignoring the rest")
        rows = rows[:MAX_ROWS]
    return rows, warnings


def _read_xlsx(path: str) -> list[list]:
    try:
        import openpyxl
    except ImportError:                                     # pragma: no cover
        raise SystemExit("openpyxl is required to read .xlsx baskets:\n"
                         "    .venv/bin/pip install openpyxl")
    wb = openpyxl.load_workbook(path, data_only=True)
    return [list(r) for r in wb[wb.sheetnames[0]].iter_rows(values_only=True)]


# -------------------------------------------------------------- matching ---
class ExactIndex:
    """(set, product-name) -> rows, plus product-name -> rows.

    The scanner searches by relevance because it cannot trust its input.
    A basket names the product, so an exact key is both faster and - more
    importantly - incapable of returning a confident wrong answer.
    """

    def __init__(self, rows):
        self.by_set_name: dict[tuple, list] = defaultdict(list)
        self.by_name: dict[str, list] = defaultdict(list)
        for row in rows:
            name = _norm(row.get("product-name"))
            console = _norm(row.get("console-name"))
            if not name:
                continue
            self.by_set_name[(console, name)].append(row)
            self.by_name[name].append(row)

    def lookup(self, name: str, set_name: str = "") -> tuple[list, str]:
        """(candidate rows, how). Empty list means no exact hit."""
        key = _norm(name)
        if set_name:
            hit = self.by_set_name.get((_norm(set_name), key))
            if hit:
                return hit, "exact set + name"
            # The set was given but did not match: try a looser set match
            # before giving up, since "Base Set" and "Pokemon Base Set" are
            # the same shelf to a human.
            want = _norm(set_name)
            loose = [r for r in self.by_name.get(key, [])
                     if want in _norm(r.get("console-name"))
                     or _norm(r.get("console-name")) in want]
            if loose:
                return loose, "exact name, fuzzy set"
        hit = self.by_name.get(key)
        if hit:
            return hit, "exact name"
        return [], ""


def price_row(row: dict, index: ExactIndex, guide: PriceGuide,
              api_budget: list[int]) -> dict:
    """Price one basket line. Never guesses between conflicting products."""
    name, set_name, grade_text = row["name"], row["set"], row["grade"]

    # Grade parsing is the scanner's, so "PSA 8.5", "BGS 9", "CGC 10",
    # "SGC 92" and "raw" all mean here exactly what they mean in a scan.
    gi = grade_info(f"{name} {grade_text}" if grade_text else name)
    # grade_info reports its grades as strings; price_guide casts them with
    # float() before use and so must we, or _guide_cents compares str to
    # float and dies on the first graded row.
    grader = gi[0] if gi else None
    printed = _as_float(gi[1]) if gi else None
    eff = _as_float(gi[2]) if gi else None

    out = dict(row, grader=grader or "", printed_grade=printed,
               effective_grade=eff, price=None, source="", how="",
               matched_name="", matched_set="", note="")

    candidates, how_matched = index.lookup(name, set_name)

    if len(candidates) > 1:
        # Collapse duplicates of the SAME product across files (a set can
        # appear in more than one CSV); genuine rivals are different sets.
        sets = {_norm(c.get("console-name")) for c in candidates}
        if len(sets) > 1:
            shown = sorted({str(c.get("console-name")) for c in candidates})
            out["note"] = ("ambiguous: matches %d sets - add a Set column "
                           "to choose (%s)" % (len(sets), "; ".join(shown[:4])))
            return out
        candidates = candidates[:1]

    if candidates:
        product = candidates[0]
        cents, how = _guide_cents(product, eff, grader=grader,
                                  printed_grade=printed)
        out.update(matched_name=product.get("product-name") or "",
                   matched_set=product.get("console-name") or "",
                   source=f"local CSV ({how_matched})", how=how)
        if cents is None:
            out["note"] = how or "no usable price field for this grade"
        else:
            out["price"] = round(cents / 100.0, 2)
        return out

    # No exact hit: fall back to the scanner's own resolution, which may
    # reach the paid API. Capped so a 500-row basket cannot drain quota.
    if api_budget[0] <= 0:
        out["note"] = "not in local CSVs (API cap reached)"
        return out
    api_budget[0] -= 1
    query = f"{name} {grade_text}".strip()
    quote = guide.quote(identity_of(query))
    if quote.value:
        out.update(price=round(float(quote.value), 2),
                   source=f"guide lookup ({quote.match})",
                   how=quote.how, matched_name=quote.product_name or "",
                   matched_set=quote.console_name or "")
    else:
        out["note"] = quote.note or "not found in CSVs or guide"
    return out


# --------------------------------------------------------------- seeding ---
# Sealed product sits in the same catalogue as singles: the 1st Edition
# Base Set listing includes a $14,179 Booster Box and a $10,108 Blister
# Pack. Priced as if they were PSA 10 cards they added ~$32k to a set
# total that was supposed to be "all 100+ cards".
_SEALED_WORDS = r"booster|box|blister|deck|tin|bundle|pack|collection|case"
_SEALED_RE = None
_NUMBER_RE = None


def _is_sealed(name: str) -> bool:
    """Sealed product rather than a single card.

    Word boundaries matter here: a substring test calls 'Dratini' and
    'Fighting Energy' sealed because they contain 'tin'. The decisive
    signal is the card number - singles carry one, sealed product does
    not - with the keyword only used to confirm.
    """
    global _SEALED_RE, _NUMBER_RE
    import re
    if _SEALED_RE is None:
        _SEALED_RE = re.compile(rf"\b({_SEALED_WORDS})\b", re.I)
        _NUMBER_RE = re.compile(r"#\s*\d+")
    if _NUMBER_RE.search(name or ""):
        return False
    return bool(_SEALED_RE.search(name or ""))


def seed_set(index_rows, set_name: str, variant: str = "",
             include_sealed: bool = False) -> tuple[list[dict], list[dict]]:
    """(card rows, sealed rows) for a set, ready for grades.

    Typing 109 card names by hand is how a basket gets typos, and a typo
    here is a silently missing line item rather than an error.
    """
    want = _norm(set_name)
    cards, sealed = [], []
    for row in index_rows:
        console = _norm(row.get("console-name"))
        if console != want and want not in console:
            continue
        name = str(row.get("product-name") or "")
        if variant and _norm(variant) not in _norm(name):
            continue
        entry = {"name": name, "set": str(row.get("console-name") or ""),
                 "grade": ""}
        (sealed if _is_sealed(name) else cards).append(entry)
    if include_sealed:
        cards += sealed
        sealed = []
    cards.sort(key=lambda r: _card_sort_key(r["name"]))
    sealed.sort(key=lambda r: _card_sort_key(r["name"]))
    return cards, sealed


def _card_sort_key(name: str):
    """Sort by card number when there is one, so a set reads in order."""
    import re
    m = re.search(r"#\s*([0-9]+)", name)
    return (0, int(m.group(1)), name) if m else (1, 0, name)


# ---------------------------------------------------------------- output ---
def write_report(path: str, priced: list[dict], warnings: list[str],
                 basket_name: str) -> None:
    try:
        import openpyxl
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
    except ImportError:                                     # pragma: no cover
        raise SystemExit("openpyxl is required:\n"
                         "    .venv/bin/pip install openpyxl")

    ok = [r for r in priced if r["price"] is not None]
    bad = [r for r in priced if r["price"] is None]
    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "Priced"
    headers = ["Line", "Card", "Set", "Grade", "Price", "Matched Product",
               "Matched Set", "Source", "Price Field"]
    ws.append(headers)
    for r in ok:
        ws.append([r["line"], r["name"], r["set"], r["grade"], r["price"],
                   r["matched_name"], r["matched_set"], r["source"],
                   r["how"]])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for i, w in enumerate([6, 42, 26, 10, 14, 42, 26, 26, 34], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2, min_col=5, max_col=5):
        for c in row:
            c.number_format = '"$"#,##0.00'

    ws2 = wb.create_sheet("Not Priced")
    ws2.append(["Line", "Card", "Set", "Grade", "Why"])
    for r in bad:
        ws2.append([r["line"], r["name"], r["set"], r["grade"],
                    r["note"] or "no price"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    ws2.freeze_panes = "A2"
    for i, w in enumerate([6, 42, 26, 10, 68], start=1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    ws3 = wb.create_sheet("Summary")
    total = sum(r["price"] for r in ok)
    local = sum(1 for r in ok if r["source"].startswith("local"))
    lines = [
        ("Basket", basket_name),
        ("Line items supplied", len(priced)),
        ("Priced", len(ok)),
        ("Not priced", len(bad)),
        ("", ""),
        ("Total value", total),
        ("Priced from local CSV (no API calls)", local),
        ("Priced via guide lookup", len(ok) - local),
        ("", ""),
        ("NOTE", "A basket total is guide value, not a sale price. It "
                 "ignores fees, shipping and the fact that selling 100 "
                 "cards at once moves the market."),
    ]
    for label, value in lines:
        ws3.append([label, value])
    for w in warnings:
        ws3.append(["WARNING", w])
    ws3["B6"].number_format = '"$"#,##0.00'
    for c in ws3["A"]:
        c.font = Font(bold=True)
    ws3.column_dimensions["A"].width = 38
    ws3.column_dimensions["B"].width = 76

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb.save(path)


def write_seed(path: str, rows: list[dict]) -> None:
    import openpyxl
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Basket"
    ws.append(["Card", "Set", "Grade"])
    for r in rows:
        ws.append([r["name"], r["set"], r["grade"]])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for i, w in enumerate([46, 28, 12], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb.save(path)


# ------------------------------------------------------------------ main ---
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Price a basket of cards.")
    p.add_argument("--in", dest="infile", help="basket .xlsx or .csv")
    p.add_argument("-o", "--out", help="where to write the priced workbook")
    p.add_argument("--seed-set", help="write a starter basket for a set")
    p.add_argument("--variant", default="",
                   help="only products whose name contains this "
                        "(e.g. '1st Edition')")
    p.add_argument("--api-cap", type=int, default=50,
                   help="most rows allowed to reach the paid API (default 50)")
    p.add_argument("--include-sealed", action="store_true",
                   help="keep booster boxes/packs in a seeded set "
                        "(default: cards only)")
    p.add_argument("--list-sets", action="store_true",
                   help="show set names the local CSVs cover")
    args = p.parse_args(argv)

    config = scanner.load_config(os.path.join(ROOT, "config.yaml"))
    index = load_index(ROOT)
    if not len(index):
        print("No price-guide CSVs found. Run 'Download Price Guides.command'")
        return 1

    if args.list_sets:
        counts: dict[str, int] = defaultdict(int)
        for row in index.rows:
            counts[str(row.get("console-name") or "?")] += 1
        for name, n in sorted(counts.items(), key=lambda kv: -kv[1])[:60]:
            print(f"  {n:>6}  {name}")
        print(f"\n{len(counts):,} sets covered by {len(index):,} products.")
        return 0

    if args.seed_set:
        rows, sealed = seed_set(index.rows, args.seed_set, args.variant,
                                include_sealed=args.include_sealed)
        if not rows:
            print(f"No products found for set {args.seed_set!r}"
                  + (f" with variant {args.variant!r}" if args.variant else "")
                  + ".\nTry --list-sets to see the exact names.")
            return 1
        label = args.seed_set + (f" {args.variant}" if args.variant else "")
        out = args.out or os.path.join(
            BASKET_DIR, f"{_slug(label)}.xlsx")
        write_seed(out, rows)
        print(f"Wrote {len(rows)} card line items -> {out}")
        if sealed:
            print(f"\nLeft out {len(sealed)} sealed product(s) - they are in "
                  "the same\ncatalogue but are not single cards, and priced "
                  "as slabs they would\ninflate the set total:")
            for s in sealed:
                print(f"    {s['name']}")
            print("  Add them with --include-sealed if you want them.")
        print("\nFill in the Grade column (blank = ungraded), then price it:")
        print(f'  .venv/bin/python code/basket_pricer.py --in "{out}"')
        return 0

    if not args.infile:
        p.error("give --in <basket file>, or --seed-set to make one")

    rows, warnings = read_basket(args.infile)
    if not rows:
        print(f"No card rows found in {args.infile}")
        for w in warnings:
            print(f"  {w}")
        return 1

    guide = PriceGuide(config)
    exact = ExactIndex(index.rows)
    budget = [max(0, args.api_cap)]
    priced = [price_row(r, exact, guide, budget) for r in rows]

    basket_name = os.path.basename(args.infile)
    out = args.out or os.path.join(
        ROOT, "reports", f"basket_{_slug(os.path.splitext(basket_name)[0])}.xlsx")
    write_report(out, priced, warnings, basket_name)

    ok = [r for r in priced if r["price"] is not None]
    total = sum(r["price"] for r in ok)
    local = sum(1 for r in ok if r["source"].startswith("local"))
    print(f"  priced      {len(ok)} of {len(priced)}")
    print(f"  total       ${total:,.2f}")
    print(f"  local CSV   {local} (no API calls)   guide lookups {len(ok)-local}")
    if len(priced) - len(ok):
        print(f"  not priced  {len(priced)-len(ok)} - see the 'Not Priced' tab")
    for w in warnings:
        print(f"  note: {w}")
    print(f"\n  -> {out}")
    return 0


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", str(text).casefold()).strip("-") or "basket"


if __name__ == "__main__":
    raise SystemExit(main())
