"""NFL DraftKings scoring — the actuals side of a football DFS backtest.

The MLB verticals score realized DK points from the banked box scores
(``velocity.models.dfs_mlb``); this is the football equivalent, over
nflverse's weekly player stats. It exists first because a backtest is only
as honest as its actuals: NFL Showdown is the second-largest pool DK runs
(28,603 entries on 25 Aug 2026) and Velocity's football DFS surface has
never been scored against anything.

Two details separate a real DK score from an approximation, and both matter:

* **Milestone bonuses.** DK pays +3 at 300 passing / 100 rushing / 100
  receiving yards. Projections deliberately leave them out — a bonus is a
  tail event and adding it at the mean overstates every player
  (``velocity.dfs.scoring``) — but *actuals* must include them, because DK
  paid them. A backtest that drops the bonus quietly under-scores exactly
  the ceiling games tournaments are won on.
* **The negative columns.** Interceptions and lost fumbles are −1 each, and
  nflverse splits fumbles across rushing, receiving and sack columns.

Kickers are scored too, because DK's Showdown boards roster them (its
classic roster does not) and a kicker is routinely a live captain. DK pays
by distance — 3 / 4 / 5 points at 0-39, 40-49 and 50+ — and does not charge
for a miss.

Pure functions of frames; offline-testable, no network.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pandas as pd

# DK NFL classic scoring, per unit.
DK_NFL_POINTS = {
    "pass_yards": 0.04,
    "pass_tds": 4.0,
    "interceptions": -1.0,
    "rush_yards": 0.1,
    "rush_tds": 6.0,
    "receptions": 1.0,  # full PPR
    "receiving_yards": 0.1,
    "receiving_tds": 6.0,
    "return_tds": 6.0,
    "two_point_conversions": 2.0,
    "fumbles_lost": -1.0,
}
# Milestone bonuses: +3 once the threshold is reached, not per yard.
DK_NFL_BONUSES = (
    ("pass_yards", 300.0, 3.0),
    ("rush_yards", 100.0, 3.0),
    ("receiving_yards", 100.0, 3.0),
)
# Kickers score by DISTANCE, and only on a Showdown board — DK's classic
# roster has no kicker slot, but its single-game format does, and a kicker
# is routinely a live captain. Misses cost nothing in DK's NFL rules.
DK_NFL_KICKING = {
    "fg_made_0_39": 3.0,
    "fg_made_40_49": 4.0,
    "fg_made_50_plus": 5.0,
    "pat_made": 1.0,
}


def nfl_dk_points(frame: pd.DataFrame, *, bonuses: bool = True) -> pd.Series:
    """DK points scored, per player-week row.

    ``bonuses`` is on for actuals and should be **off** when scoring a
    projection: at the mean, a 240-yard passer would collect three points he
    only earns in the tail.
    """
    def col(name: str) -> pd.Series:
        if name not in frame.columns:
            return pd.Series(0.0, index=frame.index)
        return pd.to_numeric(frame[name], errors="coerce").fillna(0.0)

    total = pd.Series(0.0, index=frame.index)
    for name, weight in DK_NFL_POINTS.items():
        total += col(name) * weight
    for name, weight in DK_NFL_KICKING.items():
        total += col(name) * weight
    if bonuses:
        for name, threshold, award in DK_NFL_BONUSES:
            total += (col(name) >= threshold).astype(float) * award
    return total


# --- projection ----------------------------------------------------------------
# A skill player's DK rate is far steadier than a baseball hitter's: per-game
# DK points correlate r = 0.79-0.83 season over season for players with eight
# or more games (2023->2024 and 2024->2025 on the banked weeks). So the rate
# itself carries most of the signal, and the modelling question is what to
# shrink it toward and what context to add on top.

# Positions DK actually rosters on a football board. Everyone else in the
# nflverse weekly frame is a defender who scores nothing.
SKILL_POSITIONS = ("QB", "RB", "WR", "TE")
# Showdown boards also roster kickers, so the projection has to cover them.
SHOWDOWN_POSITIONS = ("QB", "RB", "WR", "TE", "K")
# Games of prior. Roughly a quarter season: a two-game call-up must not
# project like a starter, and a full-season starter is barely moved.
PLAYER_PRIOR_GAMES = 4.0
# The opponent term shrinks harder than the player term — a defense's DK
# points allowed is a noisier estimate than a player's own production.
DEFENSE_PRIOR_GAMES = 24.0
# Context states a lean, never runs away on a thin sample.
DEFENSE_CLAMP = (0.85, 1.18)


@dataclass(frozen=True)
class DfsNflModel:
    """Fitted DK-point rates per player, plus an optional defense multiplier."""

    position_rate: Mapping[str, float]  # league DK points per game, by position
    player_rate: Mapping[str, float]
    player_position: Mapping[str, str]
    # (defense team, position) -> multiplier on a player of that position.
    defense_factor: Mapping[tuple[str, str], float] = field(default_factory=dict)

    def project(
        self, player_id: str, *, opponent: str | None = None,
        use_defense: bool = False,
    ) -> float | None:
        """Expected DK points for one player in one game, or None if unseen.

        ``use_defense`` is **off by default until it earns its place**, the
        same discipline the MLB pitcher context term got: an opponent
        adjustment is easy to write and easy to fool yourself with.
        """
        rate = self.player_rate.get(str(player_id))
        if rate is None:
            return None
        if use_defense and opponent is not None:
            position = self.player_position.get(str(player_id), "")
            rate *= self.defense_factor.get((str(opponent), position), 1.0)
        return float(rate)

    @classmethod
    def fit(cls, weeks: pd.DataFrame, *,
            positions: tuple[str, ...] = SKILL_POSITIONS) -> DfsNflModel:
        """Fit per-player DK rates from banked player-weeks.

        Each player shrinks toward **his own position's** mean, not a single
        league mean: a quarterback averages 15.2 DK points a game and a tight
        end 5.7, so one pooled prior would drag every quarterback down and
        every tight end up.
        """
        if weeks.empty:
            return cls({}, {}, {})
        frame = weeks.copy()
        frame["player_id"] = frame["player_id"].astype(str)
        frame["position"] = frame["position"].astype(str)
        frame = frame[frame["position"].isin(positions)]
        if frame.empty:
            return cls({}, {}, {})
        if "dk_points" not in frame.columns:
            frame["dk_points"] = nfl_dk_points(frame)
        frame["dk_points"] = pd.to_numeric(frame["dk_points"], errors="coerce")
        frame = frame.dropna(subset=["dk_points"])

        position_rate = frame.groupby("position")["dk_points"].mean().to_dict()
        by_player = frame.groupby(["player_id", "position"]).agg(
            dk=("dk_points", "sum"), games=("dk_points", "size")).reset_index()
        # Empirical Bayes with a per-row prior: the mass added is the
        # player's OWN position mean, not one league constant.
        league = by_player["position"].map(position_rate).astype(float)
        rates = ((by_player["dk"] + league * PLAYER_PRIOR_GAMES)
                 / (by_player["games"] + PLAYER_PRIOR_GAMES))
        player_rate = dict(zip(by_player["player_id"], rates.astype(float),
                               strict=True))
        player_position = dict(zip(by_player["player_id"], by_player["position"],
                                   strict=True))

        defense_factor: dict[tuple[str, str], float] = {}
        if "opponent" in frame.columns:
            allowed = frame.groupby(["opponent", "position"]).agg(
                dk=("dk_points", "sum"), games=("dk_points", "size")).reset_index()
            league_allowed = allowed["position"].map(position_rate).astype(float)
            per_game = ((allowed["dk"] + league_allowed * DEFENSE_PRIOR_GAMES)
                        / (allowed["games"] + DEFENSE_PRIOR_GAMES))
            ratio = (per_game / league_allowed).clip(*DEFENSE_CLAMP)
            defense_factor = {
                (str(team), str(position)): float(value)
                for team, position, value in zip(
                    allowed["opponent"], allowed["position"], ratio, strict=True)
            }
        return cls(position_rate={str(k): float(v)
                                  for k, v in position_rate.items()},
                   player_rate=player_rate, player_position=player_position,
                   defense_factor=defense_factor)
