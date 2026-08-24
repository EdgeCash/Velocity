"""Home-run props — P(a batter goes deep), from batted-ball skill and context.

The headline "lottery ticket" prop, modeled honestly. A single home run is
almost pure noise; the *rate* is not. Statcast measures the swing that
produces home runs (how hard, at what angle) rather than the rare outcome
itself, so it stabilizes far sooner — measured on our own pull, last
season's barrel rate predicts this season's HR/PA at r² ≈ 0.39, edging
prior-season HR/PA itself (0.36). The market anchors on the counting stat;
the batted-ball rate knows first.

The decomposition, every term fit from banked data (no imported constants):

    P(HR per PA) = batter_rate × pitcher_factor × park_factor

* **batter_rate** — empirical Bayes. The batter's observed HR/PA shrinks
  toward a *Statcast-informed* prior (his barrel rate mapped through a
  league-fit regression), not the league mean: a rookie with elite contact
  quality should not be priced as league average.
* **pitcher_factor** — the opposing starter's HR-allowed rate per batter
  faced, shrunk to league, as a multiplier.
* **park_factor** — each venue's HR/PA relative to league, shrunk by sample.

Then P(≥1 HR) = 1 − (1 − p)^PA over the plate appearances his lineup slot
projects (also fit from the bank). Expected HRs = PA × p ranks the salary-
free DK "Home Runs" contest, where the model competes against a field's
opinions rather than a priced line.

Weather is deliberately absent: temperature and wind genuinely move home-run
distance, but we have no banked historical weather to FIT a coefficient on,
and this model states only what it can measure (docs/PROPS_HR.md).

Pure functions of frames — offline-testable, no network.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MARKET = "batter_home_runs"

# Empirical-Bayes prior strengths, in the unit each rate is measured in.
# A batter needs real volume before his own HR/PA outruns the Statcast prior;
# pitcher and park effects are noisier still, so they shrink harder.
BATTER_PRIOR_PA = 350.0
PITCHER_PRIOR_BF = 400.0
PARK_PRIOR_PA = 6000.0
# Multiplier clamps — the model states context effects, never lets one term
# run away on a thin sample.
PITCHER_CLAMP = (0.65, 1.55)
PARK_CLAMP = (0.80, 1.30)


@dataclass(frozen=True)
class HomeRunModel:
    """Fitted HR rates and context multipliers (see the module docstring)."""

    league_rate: float  # league HR per PA
    batter_rate: Mapping[str, float]  # batter id → shrunken HR/PA
    pitcher_factor: Mapping[str, float]  # starter id → HR-allowed multiplier
    park_factor: Mapping[str, float]  # home-team (venue) → HR multiplier
    slot_pa: Mapping[int, float]  # lineup slot → expected PA for a starter
    default_pa: float = 4.0

    def rate(
        self,
        batter_id: str,
        *,
        opposing_starter: str | None = None,
        venue: str | None = None,
    ) -> float | None:
        """P(HR) for one plate appearance, or None for an unknown batter."""
        base = self.batter_rate.get(str(batter_id))
        if base is None:
            return None
        rate = base
        if opposing_starter is not None:
            rate *= self.pitcher_factor.get(str(opposing_starter), 1.0)
        if venue is not None:
            rate *= self.park_factor.get(str(venue), 1.0)
        return float(min(max(rate, 0.0), 1.0))

    def expected_pa(self, lineup_slot: int | None) -> float:
        """Projected plate appearances for a starting lineup slot."""
        if lineup_slot is None:
            return float(self.default_pa)
        return float(self.slot_pa.get(int(lineup_slot), self.default_pa))

    def probability(
        self,
        batter_id: str,
        *,
        opposing_starter: str | None = None,
        venue: str | None = None,
        lineup_slot: int | None = None,
        pa: float | None = None,
    ) -> float | None:
        """P(at least one HR) over the projected plate appearances."""
        per_pa = self.rate(batter_id, opposing_starter=opposing_starter,
                           venue=venue)
        if per_pa is None:
            return None
        n = self.expected_pa(lineup_slot) if pa is None else float(pa)
        return float(1.0 - (1.0 - per_pa) ** max(n, 0.0))

    def expected_home_runs(
        self,
        batter_id: str,
        *,
        opposing_starter: str | None = None,
        venue: str | None = None,
        lineup_slot: int | None = None,
    ) -> float | None:
        """Mean HRs — the ranking statistic for the DK single-stat contest."""
        per_pa = self.rate(batter_id, opposing_starter=opposing_starter,
                           venue=venue)
        if per_pa is None:
            return None
        return float(per_pa * self.expected_pa(lineup_slot))

    def for_game(
        self,
        *,
        opposing_starter: Mapping[str, str] | None = None,
        venue: str | None = None,
        lineup_slot: Mapping[str, int] | None = None,
    ) -> GameHRProps:
        """A game-scoped view implementing the prop-slate protocol."""
        return GameHRProps(self, dict(opposing_starter or {}), venue,
                           dict(lineup_slot or {}))

    @classmethod
    def fit(
        cls,
        batters: pd.DataFrame,
        games: pd.DataFrame,
        starters: pd.DataFrame | None = None,
        statcast: pd.DataFrame | None = None,
        *,
        season: int | None = None,
    ) -> HomeRunModel:
        """Fit every term from the banked frames.

        ``batters`` is the per-game plate-appearance bank (game_id, batter_id,
        lineup_slot, started, pa, hr); ``games`` supplies season + venue
        (home team); ``starters`` the per-game starting-pitcher lines;
        ``statcast`` the Savant leaderboard that informs the batter prior.
        ``season`` restricts the fit (walk-forward passes the season being
        predicted's history).
        """
        games = games.copy()
        games["game_id"] = games["game_id"].astype(str)
        if season is not None and "season" in games.columns:
            games = games[games["season"] == season]
        frame = batters.copy()
        frame["game_id"] = frame["game_id"].astype(str)
        frame["batter_id"] = frame["batter_id"].astype(str)
        # ``fit`` owns the venue join: a caller who already merged the games
        # frame (the walk-forward harness does) must not produce _x/_y columns.
        frame = frame.drop(columns=["home_team", "away_team"], errors="ignore")
        frame = frame.merge(
            games[["game_id", "home_team", "away_team"]], on="game_id",
            how="inner")
        for column in ("pa", "hr"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["pa", "hr"])
        frame = frame[frame["pa"] > 0]
        if frame.empty:
            return cls(0.0, {}, {}, {}, {})

        league_rate = float(frame["hr"].sum() / frame["pa"].sum())
        prior = _statcast_prior(frame, statcast, league_rate)

        by_batter = frame.groupby("batter_id").agg(
            hr=("hr", "sum"), pa=("pa", "sum"))
        prior_rate = by_batter.index.map(
            lambda pid: prior.get(str(pid), league_rate))
        batter_rate = (
            (by_batter["hr"] + np.asarray(prior_rate) * BATTER_PRIOR_PA)
            / (by_batter["pa"] + BATTER_PRIOR_PA)
        )

        park_factor = _park_factors(frame, league_rate)
        pitcher_factor = _pitcher_factors(starters, league_rate)
        slot_pa = _slot_plate_appearances(frame)
        return cls(
            league_rate=league_rate,
            batter_rate={str(k): float(v) for k, v in batter_rate.items()},
            pitcher_factor=pitcher_factor,
            park_factor=park_factor,
            slot_pa=slot_pa,
            default_pa=float(frame.loc[frame["started"].astype(bool), "pa"].mean())
            if "started" in frame.columns and frame["started"].any() else 4.0,
        )


def _statcast_prior(
    frame: pd.DataFrame, statcast: pd.DataFrame | None, league_rate: float
) -> dict[str, float]:
    """Batter id → HR/PA implied by his batted-ball quality.

    Fits ``hr_pa ~ a + b·barrel_rate`` across batters with real volume in the
    banked frame, then applies it to every player Savant measured. Without a
    Statcast frame (or without enough overlap to fit), the prior collapses to
    the league rate and the model degrades to plain shrinkage.
    """
    if statcast is None or statcast.empty or "barrel_rate" not in statcast.columns:
        return {}
    board: pd.DataFrame = statcast.copy()
    if "side" in board.columns:
        board = board[board["side"] == "batter"]
    board = board[board["barrel_rate"].notna()]
    if board.empty:
        return {}
    board = board.assign(player_id=board["player_id"].astype(str))
    board = board.drop_duplicates(subset=["player_id"], keep="last")

    observed = frame.groupby("batter_id").agg(hr=("hr", "sum"), pa=("pa", "sum"))
    observed = observed[observed["pa"] >= 150]
    joined = observed.join(board.set_index("player_id")["barrel_rate"], how="inner")
    if len(joined) < 30:
        return {}
    y = (joined["hr"] / joined["pa"]).to_numpy()
    x = joined["barrel_rate"].to_numpy()
    slope, intercept = np.polyfit(x, y, 1)
    predicted = intercept + slope * board["barrel_rate"].to_numpy()
    # A prior is a starting point, never a claim: hold it inside a sane band
    # around the league rate so an extreme Statcast reading can't invent a
    # rate no hitter has ever posted.
    predicted = np.clip(predicted, 0.2 * league_rate, 3.0 * league_rate)
    return {str(pid): float(p)
            for pid, p in zip(board["player_id"], predicted, strict=True)}


def _park_factors(frame: pd.DataFrame, league_rate: float) -> dict[str, float]:
    """Venue (home team) → HR multiplier, shrunk toward neutral by sample."""
    by_park = frame.groupby("home_team").agg(hr=("hr", "sum"), pa=("pa", "sum"))
    shrunk = (
        (by_park["hr"] + league_rate * PARK_PRIOR_PA)
        / (by_park["pa"] + PARK_PRIOR_PA)
    ) / league_rate
    shrunk = shrunk.clip(*PARK_CLAMP)
    return {str(k): float(v) for k, v in shrunk.items()}


def _pitcher_factors(
    starters: pd.DataFrame | None, league_rate: float
) -> dict[str, float]:
    """Starter → HR-allowed multiplier, shrunk toward league by batters faced."""
    if starters is None or starters.empty:
        return {}
    needed = {"starter_id", "hr", "batters_faced"}
    if not needed.issubset(starters.columns):
        return {}
    frame = starters.copy()
    for column in ("hr", "batters_faced"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["hr", "batters_faced"])
    frame = frame[frame["batters_faced"] > 0]
    if frame.empty:
        return {}
    by_pitcher = frame.groupby(frame["starter_id"].astype(str)).agg(
        hr=("hr", "sum"), bf=("batters_faced", "sum"))
    league = float(by_pitcher["hr"].sum() / by_pitcher["bf"].sum()) or league_rate
    shrunk = (
        (by_pitcher["hr"] + league * PITCHER_PRIOR_BF)
        / (by_pitcher["bf"] + PITCHER_PRIOR_BF)
    ) / league
    shrunk = shrunk.clip(*PITCHER_CLAMP)
    return {str(k): float(v) for k, v in shrunk.items()}


def _slot_plate_appearances(frame: pd.DataFrame) -> dict[int, float]:
    """Lineup slot → mean plate appearances for a batter who STARTED there.

    Leadoff hitters bat more than the nine hole; a pregame HR projection has
    to price that. Substitutes (slot 0) are excluded — they carry no
    projectable workload.
    """
    if "lineup_slot" not in frame.columns:
        return {}
    starting = frame
    if "started" in frame.columns:
        starting = frame[frame["started"].astype(bool)]
    starting = starting[starting["lineup_slot"].astype(int) > 0]
    if starting.empty:
        return {}
    means = starting.groupby(starting["lineup_slot"].astype(int))["pa"].mean()
    return {int(str(k)): float(v) for k, v in means.items()}


@dataclass(frozen=True)
class GameHRProps:
    """One game's HR distributions — the ``PropDistributions`` protocol."""

    model: HomeRunModel
    opposing_starter: Mapping[str, str] = field(default_factory=dict)
    venue: str | None = None
    lineup_slot: Mapping[str, int] = field(default_factory=dict)

    def _probability(self, player_id: str) -> float | None:
        pid = str(player_id)
        return self.model.probability(
            pid,
            opposing_starter=self.opposing_starter.get(pid),
            venue=self.venue,
            lineup_slot=self.lineup_slot.get(pid),
        )

    def has(self, player_id: str, market: str) -> bool:
        return market == MARKET and self._probability(player_id) is not None

    def prob_over(self, player_id: str, market: str, point: float) -> float:
        """P(HR total > point). The board's line is 0.5 — "goes deep"."""
        p_at_least_one = self._probability(player_id)
        if p_at_least_one is None or market != MARKET:
            return 0.5
        if point < 1.0:
            return p_at_least_one
        # Multi-HR lines are rare; price them off the same per-PA rate.
        return float(self._at_least(player_id, int(point) + 1))

    def prob_under(self, player_id: str, market: str, point: float) -> float:
        over = self.prob_over(player_id, market, point)
        return float(1.0 - over)

    def _at_least(self, player_id: str, k: int) -> float:
        """P(at least k home runs) — Binomial over the projected PAs."""
        pid = str(player_id)
        per_pa = self.model.rate(pid,
                                 opposing_starter=self.opposing_starter.get(pid),
                                 venue=self.venue)
        if per_pa is None:
            return 0.0
        n = int(round(self.model.expected_pa(self.lineup_slot.get(pid))))
        if k > n:
            return 0.0
        from math import comb
        below = sum(comb(n, i) * per_pa**i * (1 - per_pa) ** (n - i)
                    for i in range(k))
        return float(max(0.0, 1.0 - below))
