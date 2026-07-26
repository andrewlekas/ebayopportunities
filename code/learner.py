"""Self-improving price model.

Every full scan logs (auction snapshot -> actual close) pairs into the
history DB. After each scan this module refits and the engine picks up
whatever the data currently supports:

  Tier 0 (cold start): hand-tuned defaults from config.yaml.
  Tier 1 (>=20 closes): learned auction_settle_ratio = median(actual/fair),
      written to learned_params.json; overrides the config default.
  Tier 2 (>=150 closes, scikit-learn installed): gradient-boosted model
      predicting close/fair from [hours_left, cost_ratio, bids, log_fair].
      Deployed to model.pkl ONLY if its cross-validated error beats the
      Tier-1 parametric model - otherwise it stays benched and we say so.

Run `python main.py --calibrate` to see the current state.
"""
from __future__ import annotations

import collections
import json
import logging
import math
import os
from datetime import datetime, timezone

import db as histdb

log = logging.getLogger(__name__)

PARAMS_FILE = "learned_params.json"
MODEL_FILE = "model.pkl"
# Tier 1 needs this many CLOSED AUCTIONS (not snapshots - see _per_item).
MIN_PARAM_N = 20
# Tier 2 needs this many distinct closed auctions. Counting snapshots
# instead let a 4-feature model deploy off ~28 real auctions (2026-07-25).
MIN_ML_ITEMS = 150

# ---- trust filters -------------------------------------------------------
# Observations are written for every auction the scanner values, well before
# the report's $1,000 floor applies. On 2026-07-25 that meant 5,907 of 19,232
# observations carried a fair value under $10 - a graded Jungle card valued
# at $2.85 that closed at $67 is a 24x "settle ratio". Feeding those to the
# learner produced settle_ratio 1.276 (auctions supposedly closing 28% ABOVE
# fair), which inflated every expected cost and silently crushed EV.
DEFAULT_MIN_FAIR = 50.0          # below this the valuation isn't trustworthy
DEFAULT_MIN_COMPS = 3            # ...nor is one built on <3 matched comps
DEFAULT_RATIO_BAND = (0.1, 3.0)  # outside this the row is an error, not a close
# A model only deploys if it is ACTUALLY accurate, not merely less bad than
# a broken baseline (the old gate was "beat parametric by 3%", and parametric
# had an MAE of 1.126 - i.e. useless).
DEFAULT_MAX_CV_MAE = 0.25


def _training_rows(conn, *, min_fair: float = DEFAULT_MIN_FAIR,
                   min_comps: int = DEFAULT_MIN_COMPS,
                   ratio_band: tuple = DEFAULT_RATIO_BAND):
    """(rows, stats) where each row is
    (item_id, hours_left, cost_ratio, bids, log_fair, target, n_comps, seen).

    target = actual close / fair value. Rows the filters reject are counted
    in `stats` so scan.log shows WHY a training set is the size it is.
    """
    try:
        rows = conn.execute("""
            SELECT o.item_id, o.hours_left, o.price + o.shipping, o.bids,
                   o.fair, o.n_comps, o.observed_at, c.actual_price
            FROM observations o JOIN closed c ON o.item_id = c.item_id
            WHERE o.fair > 0 AND c.actual_price > 0""").fetchall()
    except Exception:
        # pre-migration database (no n_comps column)
        rows = [(r[0], r[1], r[2], r[3], r[4], None, r[5], r[6])
                for r in conn.execute("""
            SELECT o.item_id, o.hours_left, o.price + o.shipping, o.bids,
                   o.fair, o.observed_at, c.actual_price
            FROM observations o JOIN closed c ON o.item_id = c.item_id
            WHERE o.fair > 0 AND c.actual_price > 0""").fetchall()]
    lo, hi = ratio_band
    out, stats = [], collections.Counter()
    for item_id, hrs, cost, bids, fair, n_comps, seen, actual in rows:
        stats["joined"] += 1
        if fair < min_fair:
            stats["dropped_fair_below_floor"] += 1
            continue
        # No n_comps recorded = observation predates the evidence columns,
        # i.e. it was written by the pre-2026-07-25 valuation code. Those
        # rows are measurably untrustworthy (the closes matched to them run
        # at a median of 8x fair for 07-18, 200x for 07-14) and there is no
        # way to tell the good ones from the bad. We learn only from
        # observations that carry their own evidence.
        if n_comps is None:
            stats["dropped_no_evidence_recorded"] += 1
            continue
        if n_comps < min_comps:
            stats["dropped_thin_comps"] += 1
            continue
        target = actual / fair
        if not (lo <= target <= hi):
            stats["dropped_ratio_out_of_band"] += 1
            continue
        out.append((item_id, hrs if hrs is not None else 72.0,
                    min(cost / fair, 2.0), bids or 0,
                    math.log10(max(fair, 1)), target, n_comps, seen or ""))
        stats["kept_snapshots"] += 1
    return out, stats


def _write_params(directory: str, params: dict) -> None:
    with open(os.path.join(directory, PARAMS_FILE), "w") as f:
        json.dump(params, f, indent=2)


def _per_item(rows: list) -> list:
    """One row per auction - the best-evidenced snapshot of each.

    The same auction is observed on every sweep (5.3x on average), so a
    median taken over raw snapshots is weighted by how often a card happened
    to be scanned rather than by the market.
    """
    best: dict = {}
    for r in rows:
        rank = (r[6] if r[6] is not None else -1, r[7])
        if r[0] not in best or rank > best[r[0]][0]:
            best[r[0]] = (rank, r)
    return [v[1] for v in best.values()]


def load_params(directory: str = ".") -> dict:
    path = os.path.join(directory, PARAMS_FILE)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def fit(db_path: str = "history.db", directory: str = ".",
        config: dict | None = None) -> str:
    algo = (config or {}).get("algorithm", {}) if config else {}
    min_fair = algo.get("learner_min_fair", DEFAULT_MIN_FAIR)
    min_comps = algo.get("learner_min_comps", DEFAULT_MIN_COMPS)
    band = tuple(algo.get("learner_ratio_band") or DEFAULT_RATIO_BAND)
    max_cv_mae = algo.get("ml_max_cv_mae", DEFAULT_MAX_CV_MAE)

    conn = histdb.connect(db_path)
    snapshots, stats = _training_rows(conn, min_fair=min_fair,
                                      min_comps=min_comps, ratio_band=band)
    conn.close()
    log.info("learner: training filter -> %s",
             ", ".join(f"{k}={v}" for k, v in sorted(stats.items())))

    data = _per_item(snapshots)          # one row per closed auction
    n = len(data)
    n_snap = len(snapshots)
    if n < MIN_PARAM_N:
        # IMPORTANT: still write the params file. Returning early used to
        # leave a previously-learned (and possibly wrong) settle ratio and
        # a deployed model on disk, which the engine would keep using
        # forever. Writing n here makes ClosePredictor fall back to the
        # hand-tuned config ratio, which is the honest cold-start answer.
        _write_params(directory, {
            "n": n, "n_snapshots": n_snap,
            "filters": {"min_fair": min_fair, "min_comps": min_comps,
                        "ratio_band": list(band)},
            "training_filter": dict(stats),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "ml": {"deployed": False, "benched_why": "cold start"}})
        return (f"learner: {n}/{MIN_PARAM_N} trustworthy closes "
                f"({n_snap} snapshots) - using the hand-tuned "
                "auction_settle_ratio from config; clean data accumulates "
                "automatically as observed auctions close")

    # ---- Tier 1: parametric ----
    targets = sorted(r[5] for r in data)
    settle = targets[len(targets) // 2]
    para_mae = sum(abs(r[5] - settle) for r in data) / n

    # price-band settle ratios: cheap cards and four-figure cards settle
    # differently (liquidity, sniper depth). A band gets its own ratio only
    # once it has enough closes; everything else uses the global median.
    bands = {}
    for name, lo, hi in (("lt500", 0, 500), ("500to2000", 500, 2000),
                         ("gte2000", 2000, float("inf"))):
        b = sorted(r[5] for r in data if lo <= 10 ** r[4] < hi)
        if len(b) >= MIN_PARAM_N:
            bands[name] = round(b[len(b) // 2], 4)

    params = {"n": n, "n_snapshots": n_snap, "settle_ratio": round(settle, 4),
              "settle_bands": bands,
              "parametric_mae": round(para_mae, 4),
              "filters": {"min_fair": min_fair, "min_comps": min_comps,
                          "ratio_band": list(band)},
              "training_filter": dict(stats),
              "updated_at": datetime.now(timezone.utc).isoformat(),
              "ml": {"deployed": False}}
    msg = (f"learner: n={n} closed auctions ({n_snap} snapshots), "
           f"settle_ratio={settle:.3f} (mae {para_mae:.3f})")
    if bands:
        msg += "; bands " + ", ".join(f"{k}={v:.3f}" for k, v in bands.items())

    # ---- Tier 2: ML, gated on real accuracy AND on beating Tier 1 ----
    if n >= MIN_ML_ITEMS:
        try:
            import joblib
            import numpy as np
            from sklearn.ensemble import GradientBoostingRegressor
            from sklearn.model_selection import GroupKFold, cross_val_score

            # Train on every snapshot (each is a genuine feature vector) but
            # SPLIT BY AUCTION: the same item appearing in both the train and
            # test fold made the old cv_mae 0.287 look far better than it was.
            X = np.array([r[1:5] for r in snapshots])
            y = np.array([r[5] for r in snapshots])
            groups = np.array([r[0] for r in snapshots])
            model = GradientBoostingRegressor(
                n_estimators=200, max_depth=3, learning_rate=0.05,
                subsample=0.8, random_state=42)
            cv = GroupKFold(n_splits=min(5, len(set(groups))))
            cv_mae = -cross_val_score(
                model, X, y, cv=cv, groups=groups,
                scoring="neg_mean_absolute_error").mean()
            beats_parametric = cv_mae < para_mae * 0.97
            accurate = cv_mae <= max_cv_mae
            if beats_parametric and accurate:
                model.fit(X, y)
                joblib.dump(model, os.path.join(directory, MODEL_FILE))
                params["ml"] = {"deployed": True, "cv_mae": round(cv_mae, 4),
                                "cv": "GroupKFold by item"}
                msg += (f"; ML DEPLOYED (grouped cv mae {cv_mae:.3f} beats "
                        f"{para_mae:.3f} and clears the {max_cv_mae:.2f} bar)")
            else:
                why = ("does not beat parametric" if not beats_parametric
                       else f"above the {max_cv_mae:.2f} accuracy bar")
                params["ml"] = {"deployed": False, "cv_mae": round(cv_mae, 4),
                                "cv": "GroupKFold by item", "benched_why": why}
                msg += f"; ML benched (grouped cv mae {cv_mae:.3f} {why})"
        except ImportError:
            msg += "; scikit-learn not installed - ML tier skipped"
        except Exception as e:                 # never let learning kill a scan
            log.warning("learner: ML fit failed (%s)", e)
    else:
        msg += f"; ML tier at {n}/{MIN_ML_ITEMS} closed auctions"

    if not (params["ml"] or {}).get("deployed"):
        if os.path.exists(os.path.join(directory, MODEL_FILE)):
            log.info("learner: model.pkl exists on disk but is NOT in use "
                     "(ML not deployed) - the parametric settle ratio is "
                     "driving auction close estimates")

    _write_params(directory, params)
    return msg


class ClosePredictor:
    """Runtime predictor used by the valuation engine."""

    def __init__(self, directory: str = "."):
        self.params = load_params(directory)
        self.model = None
        if (self.params.get("ml") or {}).get("deployed"):
            try:
                import joblib
                self.model = joblib.load(os.path.join(directory, MODEL_FILE))
            except Exception as e:
                log.warning("learner: could not load model.pkl (%s)", e)

    @property
    def settle_ratio(self) -> float | None:
        if self.params.get("n", 0) >= MIN_PARAM_N:
            return self.params.get("settle_ratio")
        return None

    def settle_ratio_for(self, fair: float) -> float | None:
        """Price-band settle ratio when that band has enough closes;
        falls back to the global learned ratio, then None (config)."""
        bands = self.params.get("settle_bands") or {}
        if fair < 500:
            band = bands.get("lt500")
        elif fair < 2000:
            band = bands.get("500to2000")
        else:
            band = bands.get("gte2000")
        return band or self.settle_ratio

    def predict_ratio(self, hours_left, cost_ratio, bids, fair) -> float | None:
        """Predicted close/fair for an auction, or None if no ML model."""
        if not self.model:
            return None
        import math as _m
        x = [[hours_left if hours_left is not None else 72.0,
              min(cost_ratio, 2.0), bids or 0, _m.log10(max(fair, 1))]]
        pred = float(self.model.predict(x)[0])
        return min(max(pred, cost_ratio), 1.5)   # sanity clamp
