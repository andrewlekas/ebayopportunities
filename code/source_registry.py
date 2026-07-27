"""Manifest-driven marketplace connector registry.

Adding an authorized JSON/CSV source should require a manifest and feed, not
editing scanner orchestration code. Manifests describe access, field mapping,
capabilities, and acquisition economics; secrets remain in ignored config or
environment variables.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import partial

import yaml

log = logging.getLogger(__name__)

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
CAPABILITIES = {"auctions", "fixed"}
RAW_SECRET_KEYS = {
    "token", "access_token", "api_key", "secret", "client_secret",
    "password",
}
TOP_LEVEL_KEYS = {
    "id", "display_name", "enabled", "capabilities", "access",
    "field_map", "economics",
}
ACCESS_KEYS = {
    "feed_file", "authorized", "endpoint", "access_token_env",
    "api_key_env", "api_key_header",
}
ECONOMIC_KEYS = {
    "buyer_fee_rate", "auction_buyer_fee_rate", "fixed_buyer_fee_rate",
    "minimum_buyer_fee", "shipping", "insurance_rate",
    "insurance_on_buyer_fee", "international_shipping",
    "import_duty_rate", "fx_spread_rate", "marketplace",
}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class SourceManifest:
    source_id: str
    display_name: str
    enabled: bool
    capabilities: frozenset[str]
    access: dict
    field_map: dict
    economics: dict
    path: str


def manifest_directory(config: dict) -> str:
    return os.path.join(
        config.get("_config_dir") or os.getcwd(), "source_manifests")


def _reject_embedded_secrets(value, path="manifest") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key).strip().lower()
            if name in RAW_SECRET_KEYS and child not in (None, ""):
                raise ManifestError(
                    f"{path}.{key}: raw secrets are forbidden; use an "
                    "environment-variable name or secrets.yaml")
            _reject_embedded_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_embedded_secrets(child, f"{path}[{index}]")


def _mapping(value, field: str) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ManifestError(f"{field} must be a mapping")
    return dict(value)


def validate_manifest(data: dict, path="<manifest>") -> SourceManifest:
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be a mapping")
    _reject_embedded_secrets(data)
    unknown = set(data) - TOP_LEVEL_KEYS
    if unknown:
        raise ManifestError(
            "unknown top-level field(s): " + ", ".join(sorted(unknown)))
    source_id = str(data.get("id") or "").strip()
    if not ID_RE.fullmatch(source_id):
        raise ManifestError(
            "id must match ^[a-z][a-z0-9_]{1,40}$")
    display = str(data.get("display_name") or source_id).strip()
    if not display:
        raise ManifestError("display_name cannot be blank")
    if "enabled" in data and not isinstance(data["enabled"], bool):
        raise ManifestError("enabled must be true or false")
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ManifestError(
            "capabilities must be a non-empty list")
    capability_set = frozenset(str(item).strip().lower()
                               for item in capabilities)
    bad = capability_set - CAPABILITIES
    if bad:
        raise ManifestError(
            "unsupported capabilities: " + ", ".join(sorted(bad)))

    access = _mapping(data.get("access"), "access")
    unknown_access = set(access) - ACCESS_KEYS
    if unknown_access:
        raise ManifestError(
            "unknown access field(s): "
            + ", ".join(sorted(unknown_access)))
    if ("authorized" in access
            and not isinstance(access["authorized"], bool)):
        raise ManifestError("access.authorized must be true or false")
    field_map = _mapping(data.get("field_map"), "field_map")
    for required in ("title", "url", "current_price"):
        if not str(field_map.get(required) or "").strip():
            raise ManifestError(
                f"field_map.{required} is required")
    if any(not isinstance(key, str) or not isinstance(value, str)
           for key, value in field_map.items()):
        raise ManifestError("field_map keys and values must be strings")

    economics = _mapping(data.get("economics"), "economics")
    unknown_economics = set(economics) - ECONOMIC_KEYS
    if unknown_economics:
        raise ManifestError(
            "unknown economics field(s): "
            + ", ".join(sorted(unknown_economics)))
    for key, value in economics.items():
        if key in {"insurance_on_buyer_fee", "marketplace"}:
            continue
        try:
            float(value)
        except (TypeError, ValueError):
            raise ManifestError(
                f"economics.{key} must be numeric") from None

    return SourceManifest(
        source_id=source_id,
        display_name=display,
        enabled=bool(data.get("enabled", False)),
        capabilities=capability_set,
        access=access,
        field_map=field_map,
        economics=economics,
        path=os.path.abspath(path),
    )


def validate_manifest_file(path: str) -> SourceManifest:
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"could not read YAML: {exc}") from exc
    return validate_manifest(data, path)


def load_manifests(config: dict) -> list[SourceManifest]:
    directory = manifest_directory(config)
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    manifests = []
    seen = set()
    for name in names:
        if name.startswith("_") or not name.lower().endswith(
                (".yaml", ".yml")):
            continue
        path = os.path.join(directory, name)
        try:
            manifest = validate_manifest_file(path)
            if manifest.source_id in seen:
                raise ManifestError(
                    f"duplicate id {manifest.source_id!r}")
            seen.add(manifest.source_id)
            manifests.append(manifest)
        except ManifestError as exc:
            log.error("source manifest %s ignored: %s", path, exc)
    return manifests


def _manifest_factory(manifest: SourceManifest, config: dict):
    from scrapers.manifest_feed import ManifestFeedScraper
    return ManifestFeedScraper(config, manifest)


def scraper_registry(config: dict) -> dict:
    from scrapers import ALL_SCRAPERS
    registry = dict(ALL_SCRAPERS)
    for manifest in load_manifests(config):
        if not manifest.enabled:
            continue
        if manifest.source_id in registry:
            log.error(
                "source manifest %s conflicts with built-in connector; "
                "ignored", manifest.source_id)
            continue
        registry[manifest.source_id] = partial(
            _manifest_factory, manifest)
    return registry


def enabled_source_ids(config: dict) -> list[str]:
    return [manifest.source_id for manifest in load_manifests(config)
            if manifest.enabled]


def source_health_sources(
        config: dict) -> dict[str, tuple[bool, str]]:
    rows = {}
    for manifest in load_manifests(config):
        access = manifest.access
        credentials = (
            (config.get("api_keys") or {}).get(manifest.source_id) or {})
        ready = bool(
            credentials.get("feed_file") or access.get("feed_file")
            or (credentials.get("authorized",
                                access.get("authorized", False))
                and (credentials.get("endpoint")
                     or access.get("endpoint"))))
        description = (
            f"{manifest.display_name} manifest-driven authorized feed"
            + ("" if ready else "; access not configured"))
        rows[f"{manifest.source_id}/listings"] = (
            manifest.enabled and ready, description)
    return rows
