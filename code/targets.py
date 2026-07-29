"""Structured scan targets and personal set-needs.

Hand-written marketplace phrases are useful for discovery but poor identity
contracts. This module turns explicit year/set/player/number/grade fields into
exact priority searches and validates every returned title against that
contract before the listing can consume valuation work.
"""
from __future__ import annotations

from copy import deepcopy

from valuation.comps import grade_conflict, years_in
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


def sports_target_entries(config: dict) -> list[dict]:
    """Expand one structured card target into one query per desired grade."""
    out: list[dict] = []
    for target in config.get("sports_targets") or []:
        if not isinstance(target, dict):
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
    ordered = (set_need_entries(config) + sports_target_entries(config)
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


def structured_target_mismatch(target_query: str, title: str) -> str | None:
    """Return a strict mismatch reason, or None when title fits the target."""
    wanted = identity_of(target_query or "")
    found = identity_of(title or "")
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
    if grade_conflict(target_query, title, assume_ungraded=True):
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
