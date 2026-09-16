"""Measure whether weather moves the home-run rate, on the banked games.

    python scripts/fit_hr_weather.py

`props_hr` prices `batter_rate × pitcher_factor × park_factor` and documented
weather as absent for want of a bank to fit it on. `build_mlb_weather.py`
banks it; this prints the measurement, in the shape the decomposition can
multiply.

Every number is measured against the game's own **park × season × month**
baseline, because without all three a weather coefficient measures the
building, the ball and the calendar instead. Closed-roof games are excluded
from the wind fit outright — there is no wind in there to measure — and
reported separately, since they are also the cleanest check that the baseline
is doing its job.

Derived from public box scores and a keyless feed; no odds data, safe to commit.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.models.hr_weather import (
    REFERENCE_TEMP_F,
    fit_linear,
    game_rates,
    rate_ratio,
    rate_ratio_table,
)

WIND_EDGES = (-30.0, -12.0, -8.0, -4.0, -1.0, 1.0, 4.0, 8.0, 12.0, 30.0)
TEMP_EDGES = (30.0, 50.0, 60.0, 70.0, 80.0, 90.0, 120.0)


def main() -> None:  # pragma: no cover - reporting orchestration
    parser = argparse.ArgumentParser(description="Fit the HR weather effect")
    parser.add_argument("--data", default="datasets/mlb")
    args = parser.parse_args()
    root = Path(args.data)

    frame = game_rates(
        pd.read_parquet(root / "batters.parquet"),
        pd.read_parquet(root / "weather.parquet"),
        pd.read_parquet(root / "games.parquet"),
    )
    outdoor = frame[~frame["roof_closed"]]
    closed = frame[frame["roof_closed"]]
    print(f"{len(frame)} games with weather and a box score "
          f"({int(frame['hr'].sum())} home runs, {int(frame['pa'].sum())} PA); "
          f"{len(outdoor)} outdoor, {len(closed)} under a closed roof\n")

    print("--- wind along the line of fire (mph, + blowing out) ---")
    print(rate_ratio_table(outdoor, "wind_out", WIND_EDGES).to_string(index=False))
    print("\n--- temperature (F) ---")
    print(rate_ratio_table(outdoor, "temp_f", TEMP_EDGES).to_string(index=False))

    print("\n--- fitted, per unit, against the park-season-month baseline ---")
    for column, centre, unit in (("wind_out", 0.0, "mph blowing out"),
                                 ("temp_f", REFERENCE_TEMP_F, "°F")):
        fit = fit_linear(outdoor, column, centre=centre)
        verdict = ("SIGNIFICANT" if fit.significant
                   else "not distinguishable from zero — do not price it")
        print(f"{column:9s} {fit.per_unit:+.5f} per {unit} (se {fit.se:.5f}) "
              f"— {verdict}")
        if fit.significant:
            for value in (-10.0, -5.0, 5.0, 10.0):
                print(f"            {value:+5.0f}: ×{fit.factor(value):.3f}")

    if len(closed):
        ratio, se = rate_ratio(closed)
        print(f"\nclosed roof: rate ratio {ratio:.3f} ± {se:.3f} against its own "
              "park-season-month baseline (a check on the baseline, not an effect)")


if __name__ == "__main__":
    main()
