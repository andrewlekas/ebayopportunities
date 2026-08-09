"""Structured scan targets and personal set-needs.

Hand-written marketplace phrases are useful for discovery but poor identity
contracts. This module turns explicit year/set/player/number/grade fields into
exact priority searches and validates every returned title against that
contract before the listing can consume valuation work.
"""
from __future__ import annotations

from copy import deepcopy

from valuation.comps import grade_conflict, grade_info, years_in
from valuation.identity import identity_of


def _query_for_target(target: dict, grade: str | None = None) -> str:
    parts = [
        str(target.get("year") or "").strip(),
        str(target.get("set") or "").strip(),
        str(target.get("player") or target.get("subject") or "").strip(),
    ]
    number = str(target.get("card_number") or target.get("number") or "").strip()
    if number:
        parts.append(number if number.startswith("#") else f"#{number}")
    parallel = str(target.get("parallel") or "").strip()
    if parallel:
        parts.append(parallel)
    serial = target.get("serial")
    if serial not in (None, ""):
        parts.append(f"/{str(serial).lstrip('/')}")
    if target.get("autograph") or target.get("auto"):
        parts.append("Auto")
    if target.get("relic"):
        parts.append("Patch")
    if grade:
        parts.append(str(grade).strip())
    return " ".join(part for part in parts if part)


def grade_band(target: dict) -> tuple[float, float] | None:
    """(low, high) PSA-equivalent grades this target will accept.

    Andrew's rule, 2026-08-08: a grade one point lower and two points
    higher is still the card he wants. `grade_band: [6, 9]` states a band
    directly; `grade: 8` becomes 7..10.

    This exists as much for RUNTIME as for matching. The old behaviour
    expanded every listed grade into its own eBay search, so 35 cards at
    five grades each meant 175 queries - and fetch was already 593s of a
    27-minute run. A band is one query whose RESULTS are filtered, which
    is the same information for a fifth of the network.
    """
    band = target.get("grade_band") or target.get("grades_between")
    if isinstance(band, (list, tuple)) and len(band) == 2:
        try:
            low, high = float(band[0]), float(band[1])
        except (TypeError, ValueError):
            return None
        return (min(low, high), max(low, high))

    centre = target.get("grade")
    if centre is None:
        grades = target.get("grades")
        if isinstance(grades, (list, tuple)) and grades:
            # An explicit list still works: take its span.
            nums = []
            for g in grades:
                gi = grade_info(str(g)) if g else None
                if gi:
                    nums.append(float(gi[1]))
            if nums:
                return (min(nums), max(nums))
        return None
    gi = grade_info(f"PSA {centre}") if not str(centre).upper().startswith(
        ("PSA", "BGS", "SGC", "CGC")) else grade_info(str(centre))
    if not gi:
        return None
    value = float(gi[1])
    low = float(target.get("grade_tolerance_low", 1))
    high = float(target.get("grade_tolerance_high", 2))
    return (max(1.0, value - low), min(10.0, value + high))


def sports_target_entries(config: dict,
                          key: str = "sports_targets") -> list[dict]:
    """Expand structured card targets into scan entries.

    One query per target when a grade band applies, one per grade only
    when a target explicitly lists them without a band.
    """
    out: list[dict] = []
    for target in config.get(key) or []:
        if not isinstance(target, dict):
            continue
        band = grade_band(target)
        if band:
            # Grade is deliberately LEFT OUT of the query text: asking eBay
            # for "PSA 8" hides the PSA 7 and 9 copies this target also
            # wants, and we can filter what comes back for free.
            query = _query_for_target(target, None)
            if not query:
                continue
            out.append({
                "query": query,
                "priority": bool(target.get("priority", True)),
                "discovery": False,
                "structured_target": True,
                "target_identity": query,
                "grade_min": band[0],
                "grade_max": band[1],
                "resale_channel": target.get("resale_channel"),
                "max_buy_price": target.get("max_buy_price"),
            })
            continue

        grades = target.get("grades")
        if grades is None:
            grades = [target.get("grade")]
        if not isinstance(grades, (list, tuple)):
            grades = [grades]
        for grade in grades or [None]:
            query = _query_for_target(target, grade)
            if not query:
                continue
            out.append({
                "query": query,
                "priority": bool(target.get("priority", True)),
                "discovery": False,
                "structured_target": True,
                "target_identity": query,
                "resale_channel": target.get("resale_channel"),
                "max_buy_price": target.get("max_buy_price"),
            })
    return out


def set_need_entries(config: dict) -> list[dict]:
    """Normalise personal set-needs into priority watchlist entries."""
    out: list[dict] = []
    for raw in config.get("set_needs") or []:
        entry = {"query": raw} if isinstance(raw, str) else deepcopy(raw)
        if not isinstance(entry, dict):
            continue
        query = str(entry.get("query") or entry.get("name") or "").strip()
        if not query:
            continue
        entry.update({
            "query": query,
            "priority": bool(entry.get("priority", True)),
            "discovery": False,
            "set_need": True,
            "value_floor_override": float(entry.get("min_value", 0) or 0),
        })
        out.append(entry)
    return out


def configured_scan_entries(config: dict) -> list[dict]:
    """Set-needs and exact targets first, then the ordinary watchlist.

    Metadata is merged case-insensitively so a need already present in the
    watchlist is searched once while retaining its zero-floor/priority flags.
    """
    ordered = (set_need_entries(config)
               + sports_target_entries(config)
               + sports_target_entries(config, key="pokemon_targets")
               + list(config.get("watchlist") or []))
    out: list[dict] = []
    positions: dict[str, int] = {}
    for raw in ordered:
        entry = {"query": raw} if isinstance(raw, str) else deepcopy(raw)
        if not isinstance(entry, dict):
            continue
        query = str(entry.get("query") or "").strip()
        if not query:
            continue
        entry["query"] = query
        key = query.casefold()
        if key not in positions:
            positions[key] = len(out)
            out.append(entry)
            continue
        current = out[positions[key]]
        for field, value in entry.items():
            if field not in current or current[field] in (None, "", False):
                current[field] = value
        current["priority"] = bool(
            current.get("priority") or entry.get("priority"))
        current["set_need"] = bool(
            current.get("set_need") or entry.get("set_need"))
        current["structured_target"] = bool(
            current.get("structured_target") or entry.get("structured_target"))
    return out


def structured_target_mismatch(target_query: str, title: str,
                               grade_min: float | None = None,
                               grade_max: float | None = None) -> str | None:
    """Return a strict mismatch reason, or None when title fits the target."""
    wanted = identity_of(target_query or "")
    found = identity_of(title or "")
    if grade_min is not None or grade_max is not None:
        # The query carries no grade when a band is in force, so the band
        # is the only grade test. An ungraded raw card is not in any band.
        if found.grade is None:
            return "target grade band wants a graded card"
        if grade_min is not None and found.grade < grade_min:
            return (f"grade {found.grade:g} below target band "
                    f"{grade_min:g}-{grade_max or 10:g}")
        if grade_max is not None and found.grade > grade_max:
            return (f"grade {found.grade:g} above target band "
                    f"{grade_min or 1:g}-{grade_max:g}")
    if wanted.object_class != "unknown" and found.object_class != wanted.object_class:
        return f"target object {wanted.object_class}, listing is {found.object_class}"
    if wanted.year and wanted.year not in years_in(title or ""):
        return f"target year {wanted.year} missing"
    if wanted.subject:
        missing = set(wanted.subject) - set(found.subject)
        if missing:
            return "target subject missing"
    if wanted.number and found.number != wanted.number:
        return f"target card #{wanted.number} not confirmed"
    if wanted.set_tokens:
        missing_sets = set(wanted.set_tokens) - set(found.set_tokens)
        if missing_sets:
            return "target set missing"
    # When a band is in force it IS the grade test: the query deliberately
    # carries no grade, which grade_conflict would read as "wants ungraded".
    if (grade_min is None and grade_max is None
            and grade_conflict(target_query, title, assume_ungraded=True)):
        return "target grade mismatch"
    if wanted.parallel and wanted.parallel != found.parallel:
        return "target parallel mismatch"
    if wanted.serial is not None and wanted.serial != found.serial:
        return "target serial mismatch"
    if wanted.is_auto and not found.is_auto:
        return "target autograph missing"
    if wanted.is_relic and not found.is_relic:
        return "target relic missing"
    return None
