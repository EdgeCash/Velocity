"""Scores-based team ratings — opponent-adjusted power ratings from final scores.

Play-by-play EPA is the richest signal, but it is not always available. When all
you have is the schedule and final scores, you can still fit a solid
opponent-adjusted power rating: regress each game's points on who scored them and
who allowed them. For every game two observations are formed::

    home_score  =  base + offense[home] + defense[away] + home_edge
    away_score  =  base + offense[away] + defense[home]

and solved by the same ridge as the EPA ratings — the penalty makes the
offense/defense split identifiable and shrinks thin samples toward league
average. ``offense``/``defense`` are in **points per game** (offense positive =
scores more; defense positive = allows more, so lower is better), ``base`` is
league-average points, and ``home_edge`` is the estimated home-field advantage in
points, learned from the data rather than assumed.

This is the fallback rating for the case where only reachable schedule data
exists; it plugs into :class:`velocity.models.game_scores.ScoresGameModel`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Tuned on a real 2023 walk-forward: ridge ≈ 25 minimized Brier/log-loss and
# calibration error for the schedule-only rating (a lighter penalty overfits the
# thin ~13-game sample). Overridable per call.
DEFAULT_RIDGE_LAMBDA = 25.0


@dataclass(frozen=True)
class ScoresRatings:
    """Opponent-adjusted points-per-game offense/defense ratings from scores."""

    offense: dict[str, float]
    defense: dict[str, float]
    base_points: float
    home_edge: float
    ridge_lambda: float
    n_games: int
    teams: tuple[str, ...] = field(default_factory=tuple)

    def expected_points(self, off_team: str, def_team: str, *, at_home: bool) -> float:
        """Expected points for ``off_team`` vs ``def_team`` (unseen teams → average)."""
        mu = self.base_points + self.offense.get(off_team, 0.0) + self.defense.get(def_team, 0.0)
        return mu + (self.home_edge if at_home else 0.0)


def fit_scores_ratings(
    games: pd.DataFrame,
    *,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
) -> ScoresRatings:
    """Fit ridge-adjusted offense/defense points ratings from played games.

    ``games`` needs ``home_team``, ``away_team``, ``home_score``, ``away_score``
    (and optionally ``neutral_site``). Unplayed games (null scores) are dropped.
    The fit is deterministic.
    """
    if ridge_lambda <= 0:
        raise ValueError("ridge_lambda must be positive for an identifiable fit")

    df = games.dropna(subset=["home_score", "away_score"])
    if df.empty:
        raise ValueError("no played games to fit (need non-null scores)")

    teams = sorted(set(df["home_team"]) | set(df["away_team"]))
    index = {team: i for i, team in enumerate(teams)}
    n_teams = len(teams)
    neutral = (
        df["neutral_site"].to_numpy(dtype=bool)
        if "neutral_site" in df.columns
        else np.zeros(len(df), dtype=bool)
    )

    # Two rows per game (home offense, away offense). Columns:
    # [intercept] + offense one-hot + defense one-hot + [home_edge].
    n_obs = 2 * len(df)
    n_cols = 1 + 2 * n_teams + 1
    home_col = n_cols - 1
    x = np.zeros((n_obs, n_cols))
    y = np.zeros(n_obs)
    x[:, 0] = 1.0

    home_idx = df["home_team"].map(index).to_numpy()
    away_idx = df["away_team"].map(index).to_numpy()
    home_score = df["home_score"].to_numpy(dtype=float)
    away_score = df["away_score"].to_numpy(dtype=float)

    rows_home = np.arange(len(df))
    rows_away = np.arange(len(df)) + len(df)
    # Home-offense observations.
    x[rows_home, 1 + home_idx] = 1.0
    x[rows_home, 1 + n_teams + away_idx] = 1.0
    x[rows_home, home_col] = np.where(neutral, 0.0, 1.0)
    y[rows_home] = home_score
    # Away-offense observations.
    x[rows_away, 1 + away_idx] = 1.0
    x[rows_away, 1 + n_teams + home_idx] = 1.0
    y[rows_away] = away_score

    # Ridge: penalize offense/defense only (intercept and home_edge unpenalized).
    penalty = np.ones(n_cols)
    penalty[0] = 0.0
    penalty[home_col] = 0.0
    beta = np.linalg.solve(x.T @ x + ridge_lambda * np.diag(penalty), x.T @ y)

    offense = {team: float(beta[1 + index[team]]) for team in teams}
    defense = {team: float(beta[1 + n_teams + index[team]]) for team in teams}
    return ScoresRatings(
        offense=offense,
        defense=defense,
        base_points=float(beta[0]),
        home_edge=float(beta[home_col]),
        ridge_lambda=ridge_lambda,
        n_games=len(df),
        teams=tuple(teams),
    )
