"""Biweekly regression test: has any model quietly degraded?

Detection, never promotion. This re-runs the walk-forward gates and compares
each metric to a committed baseline (``datasets/baselines/model_drift.json``).
It reports drift and exits non-zero when a metric falls outside tolerance —
it does NOT refit, retune, or ship anything.

That separation is deliberate. Two weeks of live results is 60-80 graded
bets, which is noise; auto-recalibrating on it would make the model chase
variance. The models already refit daily on committed history. What needs a
schedule is the *question* "did something break?" — and any parameter change
that follows has to clear the lab gate with a human deciding.

    python scripts/model_drift_check.py                 # compare to baseline
    python scripts/model_drift_check.py --update-baseline
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

BASELINE = Path("datasets/baselines/model_drift.json")
# How far a metric may move before it is called drift. Generous on purpose:
# this fires on breakage, not on noise.
TOLERANCE = {
    "hr_auc": 0.030,          # ranking power of the HR model
    "hr_calibration": 0.030,  # worst bucket gap
    "hr_top3": 0.050,         # per-slate top-3 hit rate
}


def hr_metrics(step_days: int = 14) -> dict[str, float]:
    """Walk-forward the home-run model and return its headline metrics."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from validate_hr_model import auc, calibration_table, top_k_by_slate, walk_forward

    batters = pd.read_parquet("datasets/mlb/batters.parquet")
    games = pd.read_parquet("datasets/mlb/games.parquet")
    starters = pd.read_parquet("datasets/mlb/starters.parquet")
    season = int(games["season"].max())
    graded = walk_forward(batters, games, starters, None,
                          step_days=step_days, season=season)
    graded = graded.dropna(subset=["p_model"])
    if graded.empty:
        return {}
    actual = graded["went_deep"].to_numpy()
    table = calibration_table(graded["p_model"].to_numpy(), actual)
    return {
        "hr_auc": round(auc(graded["p_model"].to_numpy(), actual), 4),
        "hr_calibration": round(float(table["gap"].abs().max()), 4),
        "hr_top3": round(top_k_by_slate(graded, "p_model", 3), 4),
        "hr_n": int(len(graded)),
    }


def compare(current: dict[str, float], baseline: dict[str, float]) -> pd.DataFrame:
    """Metric-by-metric drift verdicts."""
    rows = []
    for metric, tolerance in TOLERANCE.items():
        now = current.get(metric)
        was = baseline.get(metric)
        if now is None or was is None:
            rows.append({"metric": metric, "baseline": was, "current": now,
                         "delta": None, "tolerance": tolerance,
                         "verdict": "missing"})
            continue
        delta = float(now) - float(was)
        # Calibration gap is a "lower is better" metric; the rest are higher.
        worse = delta > tolerance if metric == "hr_calibration" else delta < -tolerance
        rows.append({"metric": metric, "baseline": was, "current": now,
                     "delta": round(delta, 4), "tolerance": tolerance,
                     "verdict": "DRIFT" if worse else "ok"})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Model drift regression check")
    parser.add_argument("--baseline", default=str(BASELINE))
    parser.add_argument("--update-baseline", action="store_true",
                        help="write current metrics as the new baseline "
                             "(a human decision, never automatic)")
    parser.add_argument("--step-days", type=int, default=14)
    args = parser.parse_args()

    print(f"drift check @ {datetime.now(UTC).isoformat()}")
    current = hr_metrics(args.step_days)
    if not current:
        raise SystemExit("no walk-forward windows produced — cannot check drift")
    print("current metrics:")
    for key, value in current.items():
        print(f"  {key:18s} {value}")

    path = Path(args.baseline)
    if args.update_baseline:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**current,
                   "recorded_at": datetime.now(UTC).strftime("%Y-%m-%d")}
        path.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nbaseline written to {path}")
        return

    if not path.exists():
        raise SystemExit(
            f"no baseline at {path} — run once with --update-baseline")
    baseline = json.loads(path.read_text())
    print(f"\nbaseline recorded {baseline.get('recorded_at', 'unknown')}")
    table = compare(current, baseline)
    print(table.to_string(index=False))

    drifted = table[table["verdict"] == "DRIFT"]
    if not drifted.empty:
        print(f"\n{len(drifted)} metric(s) drifted beyond tolerance. "
              "This is a REPORT, not a retune — investigate, then decide.")
        raise SystemExit(1)
    print("\nno drift beyond tolerance.")


if __name__ == "__main__":
    main()
