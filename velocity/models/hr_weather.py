"""Does weather move the home-run rate? — measured, not assumed.

``props_hr`` prices ``P(HR per PA) = batter_rate × pitcher_factor ×
park_factor`` and says weather is absent because there was nothing banked to
fit it on. ``velocity.ingest.mlb_weather`` banks it; this measures it, in the
shape the decomposition can multiply:

    P(HR per PA) = batter_rate × pitcher_factor × park_factor × weather_factor

**The baseline is park × season × month**, and each part of that is load
bearing. Park because Coors and Oracle are different buildings. Season because
the ball changes between them. Month because home-run rates swing hard across
a summer and so does the weather — without it a temperature coefficient
mostly measures *July*, which the park factor and the batter's own rate
already carry. What is left inside a park-month is close to weather alone.

**The estimator is a ratio, not a log-regression.** Games are counts and many
have zero home runs, so a per-game log rate is undefined. For a bin of games,
``Σ HR / Σ (PA × baseline rate)`` is the rate ratio against what those same
games would have produced in average conditions — plate-appearance weighted by
construction, defined at zero, and reported with a Poisson standard error so a
bin that says nothing looks like it says nothing.

Pure functions of frames; offline-testable, no network.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# The reference conditions the multiplier is 1.0 at: no wind along the line of
# fire, and a mild evening. Both are choices of origin, not of scale.
REFERENCE_TEMP_F = 70.0

GAME_COLUMNS = ("game_id", "park", "season", "month", "hr", "pa", "expected",
                "wind_out", "temp_f", "roof_closed")


@dataclass(frozen=True)
class WeatherFit:
    """A fitted per-unit effect on the home-run rate, with its uncertainty.

    ``per_unit`` is the multiplicative change in HR/PA per unit of the driver
    (one mph blowing straight out, or one °F). ``se`` is its standard error;
    ``games`` and ``home_runs`` are what it was measured on. ``significant``
    asks the only question that matters before pricing it: is the effect
    bigger than the noise around it?
    """

    driver: str
    per_unit: float
    se: float
    games: int
    home_runs: int

    @property
    def significant(self) -> bool:
        return bool(self.se > 0 and abs(self.per_unit) > 2.0 * self.se)

    def factor(self, value: float) -> float:
        """The multiplier at ``value`` units of this driver."""
        return float(max(1.0 + self.per_unit * value, 0.0))


def game_rates(
    batters: pd.DataFrame, weather: pd.DataFrame, games: pd.DataFrame
) -> pd.DataFrame:
    """One row per game: its home runs, its plate appearances, its conditions.

    ``expected`` is what that game's plate appearances would have produced at
    its own park-season-month's average rate, which is what every rate ratio
    below is measured against.
    """
    if batters.empty or weather.empty:
        return pd.DataFrame(columns=list(GAME_COLUMNS))
    per_game = (batters.assign(game_id=batters["game_id"].astype(str))
                .groupby("game_id", as_index=False)
                .agg(hr=("hr", "sum"), pa=("pa", "sum")))
    frame = weather.assign(game_id=weather["game_id"].astype(str)).merge(
        per_game, on="game_id", how="inner")
    if "season" in games.columns:
        seasons = (games.assign(game_id=games["game_id"].astype(str))
                   [["game_id", "season"]].drop_duplicates("game_id"))
        frame = frame.merge(seasons, on="game_id", how="left")
    else:  # pragma: no cover - every committed games frame carries it
        frame["season"] = pd.NA
    frame["month"] = pd.to_datetime(frame["date"], errors="coerce").dt.month
    frame["park"] = frame["venue"].astype(str)
    frame["wind_out"] = (pd.to_numeric(frame["wind_mph"], errors="coerce")
                         * pd.to_numeric(frame["wind_vector"], errors="coerce"))
    frame = frame[frame["pa"] > 0]
    if frame.empty:
        return pd.DataFrame(columns=list(GAME_COLUMNS))

    # The park-season-month baseline, and each game's expectation under it.
    cell = ["park", "season", "month"]
    totals = frame.groupby(cell, dropna=False)[["hr", "pa"]].transform("sum")
    baseline = totals["hr"] / totals["pa"]
    frame["expected"] = frame["pa"] * baseline
    frame["roof_closed"] = frame["roof_closed"].fillna(False).astype(bool)
    return frame[list(GAME_COLUMNS)].reset_index(drop=True)


def rate_ratio(frame: pd.DataFrame) -> tuple[float, float]:
    """``Σ HR / Σ expected`` with its Poisson standard error.

    ``(nan, nan)`` when nothing was expected, which is a bin with no games
    rather than a bin with no effect.
    """
    expected = float(frame["expected"].sum())
    observed = float(frame["hr"].sum())
    if expected <= 0:
        return float("nan"), float("nan")
    return observed / expected, float(np.sqrt(max(observed, 0.0))) / expected


def rate_ratio_table(
    frame: pd.DataFrame, column: str, edges: tuple[float, ...]
) -> pd.DataFrame:
    """The rate ratio in each bin of ``column`` — the measurement, before any fit."""
    rows = []
    values = pd.to_numeric(frame[column], errors="coerce")
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        part = frame[(values >= low) & (values < high)]
        ratio, se = rate_ratio(part)
        rows.append({"bin": f"[{low:g}, {high:g})", "games": len(part),
                     "home_runs": int(part["hr"].sum()) if len(part) else 0,
                     "rate_ratio": ratio, "se": se})
    return pd.DataFrame(rows)


def fit_linear(frame: pd.DataFrame, column: str, *, centre: float = 0.0) -> WeatherFit:
    """Weighted least squares of the rate ratio on ``column``, through 1.0 at ``centre``.

    Each game contributes its own observed/expected, weighted by ``expected``
    — so a game with more plate appearances counts for more, and a game at a
    park-month with a thin baseline counts for less. The line is forced through
    1.0 at the reference so the fitted number is a *deviation* from average
    conditions rather than a second estimate of the average itself, which the
    park factor already carries.
    """
    work = frame.dropna(subset=[column, "expected"])
    work = work[work["expected"] > 0]
    if work.empty:
        return WeatherFit(column, float("nan"), float("nan"), 0, 0)
    x = pd.to_numeric(work[column], errors="coerce").to_numpy(dtype=float) - centre
    y = work["hr"].to_numpy(dtype=float) / work["expected"].to_numpy(dtype=float)
    w = work["expected"].to_numpy(dtype=float)
    denominator = float(np.sum(w * x * x))
    if denominator <= 0:
        return WeatherFit(column, float("nan"), float("nan"), len(work),
                          int(work["hr"].sum()))
    slope = float(np.sum(w * x * (y - 1.0)) / denominator)
    residual = y - 1.0 - slope * x
    # Weighted residual variance, scaled to the slope's own denominator.
    dof = max(len(work) - 1, 1)
    sigma2 = float(np.sum(w * residual * residual) / dof)
    se = float(np.sqrt(sigma2 / denominator)) if denominator > 0 else float("nan")
    return WeatherFit(column, slope, se, len(work), int(work["hr"].sum()))
