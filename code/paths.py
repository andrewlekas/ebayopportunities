"""Where the scanner keeps its files.

The top level of the folder holds only what Andrew touches: the
double-click .command files, config.yaml, and the handoff notes.
Everything the program reads or writes lives in a named folder beside
config.yaml:

    code/            this program
    config.yaml      the only file you edit
    database/        history.db - comps, observations, closes, alerts
    model/           learned_params.json, model.pkl - the close model
    portfolio/       portfolio.csv - your positions (edit in Excel)
    reports/         Opp Runs/ and BIN runs/ - the Excel output
    logs/            scan.log (+ rotations) - evidence of every run
    test results/    test_results.log, comps_test_result.txt
    state/           machine-managed run state (cookies, breakers, lock)
    setup/           requirements.txt
    docs/            README.md, FEATURES.md

Everything resolves against the CONFIG FILE's folder, never the current
working directory, so a scan launched from anywhere still finds its data.
"""
from __future__ import annotations

import os

LOGS = "logs"
STATE = "state"
DATABASE = "database"
MODEL = "model"
PORTFOLIO = "portfolio"
REPORTS = "reports"
TEST_RESULTS = "test results"
SETUP = "setup"
DOCS = "docs"

DEFAULT_DB = os.path.join(DATABASE, "history.db")


def base_dir(config: dict | None = None,
             config_path: str | None = None) -> str:
    """The folder config.yaml lives in."""
    if config_path:
        return os.path.dirname(os.path.abspath(config_path))
    if config:
        return config.get("_config_dir") or "."
    return "."


def folder(base: str, name: str) -> str:
    """Absolute path to a subfolder, created if it doesn't exist yet.

    Falls back to `base` if the folder can't be created, so a permissions
    problem degrades to the old flat layout instead of killing a scan.
    """
    path = os.path.join(base, name)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return base
    return path


def file_in(base: str, name: str, filename: str) -> str:
    """Absolute path to a file inside one of the folders above."""
    return os.path.join(folder(base, name), filename)
