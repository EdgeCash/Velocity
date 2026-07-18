"""Opponent-adjusted team efficiency ratings.

The unit of value in football is **EPA (expected points added) per play**. A raw
EPA average rewards teams that feasted on a soft schedule, so we *opponent-adjust*:
fit a linear model where every play's EPA is explained by the offense that ran it
and the defense that faced it, plus a league-average intercept::

    epa  =  intercept  +  offense[posteam]  +  defense[defteam]  +  noise

Solved by **ridge regression**. The L2 penalty does two jobs at once:

1. **Identifiability.** Offense and defense effects are only ever observed in
   combination, so the unpenalized system is rank-deficient (add a constant to
   every offense and subtract it from every defense — same fit). The penalty
   anchors a unique solution.
2. **Shrinkage / connectivity.** Teams with few plays, or a schedule that never
   connects them to the rest of the league, are pulled toward league average
   rather than taking on wild values. ``ridge_lambda`` is expressed in
   *pseudo-plays*: it behaves like adding that many league-average plays to
   every team, so a team with far more real plays than ``ridge_lambda`` is
   barely shrunk while a thin sample regresses hard — exactly the behaviour we
   want early in a season.

Sign conventions:

* ``offense[team]`` — higher is a **better** offense (generates more EPA/play).
* ``defense[team]`` — this is EPA *allowed* relative to average, so **lower**
  (more negative) is a **better** defense.

Both are deviations from the league mean, which lives in ``league_epa``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DEFAULT_RIDGE_LAMBDA = 200.0


@dataclass(frozen=True)
class TeamRatings:
    """Fitted opponent-adjusted EPA/play ratings for one slice of plays.

    ``offense`` and ``defense`` are per-play EPA deviations from league average
    (see module docstring for sign conventions). ``league_epa`` is the fitted
    intercept — the mean EPA/play across the sample.
    """

    offense: dict[str, float]
    defense: dict[str, float]
    league_epa: float
    ridge_lambda: float
    n_plays: int
    teams: tuple[str, ...] = field(default_factory=tuple)

    def matchup_delta(self, off_team: str, def_team: str) -> float:
        """Net EPA/play *deviation* for ``off_team``'s offense vs ``def_team``.

        Excludes the league intercept, so this is the efficiency edge above (or
        below) an average matchup — the quantity a scoring model scales by pace.
        A team not seen in the fit (e.g. early in a walk-forward season) defaults
        to league average (0.0).
        """
        return self.offense.get(off_team, 0.0) + self.defense.get(def_team, 0.0)

    def expected_epa(self, off_team: str, def_team: str) -> float:
        """Absolute expected EPA/play for the matchup, including the intercept."""
        return self.league_epa + self.matchup_delta(off_team, def_team)


def fit_ratings(
    plays: pd.DataFrame,
    *,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
    epa_col: str = "epa",
) -> TeamRatings:
    """Fit ridge-adjusted offense/defense EPA/play ratings from ``plays``.

    ``plays`` must have ``posteam``, ``defteam`` and ``epa_col`` columns (the
    canonical :class:`~velocity.store.schema.Plays` frame does). Rows with a
    null team or null EPA are dropped. The fit is fully deterministic — the same
    plays and ``ridge_lambda`` always produce identical ratings.
    """
    if ridge_lambda <= 0:
        raise ValueError("ridge_lambda must be positive for an identifiable fit")

    df = plays.dropna(subset=["posteam", "defteam", epa_col])
    if df.empty:
        raise ValueError("no usable plays (need non-null posteam, defteam and epa)")

    teams = sorted(set(df["posteam"]) | set(df["defteam"]))
    index = {team: i for i, team in enumerate(teams)}
    n_teams = len(teams)
    n_plays = len(df)

    # Design matrix columns: [intercept] + offense one-hot + defense one-hot.
    n_cols = 1 + 2 * n_teams
    x = np.zeros((n_plays, n_cols))
    rows = np.arange(n_plays)
    x[:, 0] = 1.0
    off_cols = df["posteam"].map(index).to_numpy() + 1
    def_cols = df["defteam"].map(index).to_numpy() + 1 + n_teams
    x[rows, off_cols] = 1.0
    x[rows, def_cols] = 1.0
    y = df[epa_col].to_numpy(dtype=float)

    # Ridge normal equations; the intercept (column 0) is left unpenalized.
    penalty = np.ones(n_cols)
    penalty[0] = 0.0
    gram = x.T @ x + ridge_lambda * np.diag(penalty)
    beta = np.linalg.solve(gram, x.T @ y)

    intercept = float(beta[0])
    offense = {team: float(beta[1 + index[team]]) for team in teams}
    defense = {team: float(beta[1 + n_teams + index[team]]) for team in teams}

    return TeamRatings(
        offense=offense,
        defense=defense,
        league_epa=intercept,
        ridge_lambda=ridge_lambda,
        n_plays=n_plays,
        teams=tuple(teams),
    )


def team_pace(plays: pd.DataFrame) -> dict[str, float]:
    """Offensive plays per game for each team.

    Pace varies enormously in college football (tempo offenses vs. ground-and-
    pound), so totals modeling needs team-specific pace rather than a league
    constant. Computed as each team's offensive plays divided by its distinct
    games in the sample.
    """
    df = plays.dropna(subset=["posteam", "game_id"])
    grouped = df.groupby("posteam")
    plays_per_team = grouped.size()
    games_per_team = grouped["game_id"].nunique()
    pace = plays_per_team / games_per_team
    return {str(team): float(p) for team, p in pace.items()}


def matchup_pace(pace: dict[str, float], home: str, away: str, league_pace: float) -> float:
    """Expected combined offensive plays per team for a matchup.

    Each team's pace is averaged with its opponent's (both teams face the same
    game clock), falling back to the league average for teams not yet seen.
    """
    home_pace = pace.get(home, league_pace)
    away_pace = pace.get(away, league_pace)
    return 0.5 * (home_pace + away_pace)
