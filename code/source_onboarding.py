"""Validate and install manifest-driven marketplace sources."""
from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime

from source_registry import (ManifestError, manifest_directory,
                             validate_manifest_file)


def install(path: str, config_dir: str, force: bool = False) -> str:
    manifest = validate_manifest_file(path)
    directory = os.path.join(config_dir, "source_manifests")
    os.makedirs(directory, exist_ok=True)
    destination = os.path.join(directory, f"{manifest.source_id}.yaml")
    if os.path.abspath(path) == os.path.abspath(destination):
        return destination
    if os.path.exists(destination):
        if not force:
            raise ManifestError(
                f"{destination} already exists; use --force to replace it")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(destination, f"{destination}.{stamp}.bak")
    shutil.copy2(path, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate/install authorized source manifests")
    parser.add_argument("--config-dir", default=".")
    parser.add_argument("--validate")
    parser.add_argument("--install")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    config_dir = os.path.abspath(args.config_dir)
    try:
        if args.validate:
            manifest = validate_manifest_file(args.validate)
            print(
                f"VALID {manifest.source_id}: "
                f"{manifest.display_name} ({', '.join(sorted(manifest.capabilities))})")
            return 0
        if args.install:
            destination = install(args.install, config_dir, args.force)
            manifest = validate_manifest_file(destination)
            print(
                f"INSTALLED {manifest.source_id} -> {destination}")
            return 0

        directory = manifest_directory({"_config_dir": config_dir})
        os.makedirs(directory, exist_ok=True)
        names = sorted(
            name for name in os.listdir(directory)
            if not name.startswith("_")
            and name.lower().endswith((".yaml", ".yml")))
        if not names:
            print("No installed source manifests.")
            return 0
        failures = 0
        for name in names:
            path = os.path.join(directory, name)
            try:
                manifest = validate_manifest_file(path)
                state = "enabled" if manifest.enabled else "disabled"
                print(
                    f"VALID {manifest.source_id} [{state}] "
                    f"{manifest.display_name}")
            except ManifestError as exc:
                failures += 1
                print(f"INVALID {name}: {exc}")
        return 1 if failures else 0
    except ManifestError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
