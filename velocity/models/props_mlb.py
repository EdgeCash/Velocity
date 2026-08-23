"""MLB pitcher-strikeout props — the sport's headline prop (docs/PROPS.md).

The decomposition is the classic one: expected Ks = expected batters
faced × the pitcher's strikeout rate × the opposing lineup's strikeout
tendency. Every input is a shrunken estimate from the banked starters
frame (``datasets/mlb/starters.parquet``: per-start K / batters_faced /
outs), so a five-start rookie prices near league average and a
600-inning veteran prices as himself. The count distribution is a
negative binomial with dispersion fit from the same history (Ks are
mildly overdispersed vs Poisson).

Implements the ``PropDistributions`` protocol keyed by statsapi pitcher
id, so :func:`velocity.wagering.props_slate.build_prop_slate` prices it
like any other prop family.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from velocity.models.props import NegativeBinomial

MARKET = "pitcher_strikeouts"

# Shrinkage priors, in the estimate's own units: batters faced for rates,
# starts for workload. League-typical scales — a starter faces ~22/start.
RATE_PRIOR_BF = 150.0
OPP_PRIOR_BF = 400.0
BF_PRIOR_STARTS = 4.0


@dataclass
class PitcherKModel:
    """Per-pitcher K distributions from the banked starters history."""

    means: dict[str, float] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    opp_factor: dict[str, float] = field(default_factory=dict)
    league_rate: float = 0.22
    league_bf: float = 22.0
    dispersion: float = 25.0

    @classmethod
    def fit(
        cls,
        starters: pd.DataFrame,
        games: pd.DataFrame,
        *,
        window_days: float = 365.0,
        as_of: pd.Timestamp | None = None,
    ) -> PitcherKModel:
        """Fit rates on the trailing window of banked starts.

        ``games`` supplies kickoff dates and the batting-side join (the
        team that FACED each starter — the opponent-tendency estimate).
        """
        kick = games[["game_id", "kickoff", "home_team", "away_team"]].copy()
        kick["game_id"] = kick["game_id"].astype(str)
        frame = starters.copy()
        frame["game_id"] = frame["game_id"].astype(str)
        frame = frame.merge(kick, on="game_id", how="inner")
        frame["kickoff"] = pd.to_datetime(frame["kickoff"])
        cutoff = (pd.Timestamp(as_of) if as_of is not None
                  else frame["kickoff"].max())
        frame = frame[frame["kickoff"] >= cutoff - pd.Timedelta(days=window_days)]
        frame = frame.dropna(subset=["batters_faced", "k"])
        frame = frame[frame["batters_faced"] > 0]
        if frame.empty:
            return cls()

        league_rate = float(frame["k"].sum() / frame["batters_faced"].sum())
        league_bf = float(frame.groupby(["game_id", "side"])["batters_faced"]
                          .first().mean())

        # The lineup that faced each starter: home starter → away batters.
        frame["batting_team"] = frame.apply(
            lambda r: r["away_team"] if r["side"] == "home" else r["home_team"],
            axis=1,
        )
        opp = frame.groupby("batting_team").agg(
            k=("k", "sum"), bf=("batters_faced", "sum"))
        opp_factor = {
            str(team): float(
                ((row["k"] + league_rate * OPP_PRIOR_BF)
                 / (row["bf"] + OPP_PRIOR_BF)) / league_rate)
            for team, row in opp.iterrows()
        }

        per = frame.groupby("starter_id").agg(
            k=("k", "sum"), bf=("batters_faced", "sum"),
            starts=("game_id", "nunique"), name=("starter_name", "last"))
        means: dict[str, float] = {}
        names: dict[str, str] = {}
        for pid, row in per.iterrows():
            rate = ((row["k"] + league_rate * RATE_PRIOR_BF)
                    / (row["bf"] + RATE_PRIOR_BF))
            bf_exp = ((row["bf"] + league_bf * BF_PRIOR_STARTS)
                      / (row["starts"] + BF_PRIOR_STARTS))
            means[str(pid)] = float(bf_exp * rate)
            names[str(pid)] = str(row["name"])

        # Dispersion by method of moments on per-start Ks around each
        # pitcher's own mean: var = mean + mean²/r  →  r = mean²/(var−mean).
        merged = frame.merge(
            per["k"].rename("k_tot"), left_on="starter_id", right_index=True)
        mu = frame.groupby("starter_id")["k"].transform("mean")
        resid_var = float(((frame["k"] - mu) ** 2).mean())
        mean_k = float(frame["k"].mean())
        excess = resid_var - mean_k
        dispersion = (mean_k ** 2 / excess) if excess > 0.05 else 100.0
        dispersion = min(max(dispersion, 5.0), 100.0)
        del merged

        return cls(means=means, names=names, opp_factor=opp_factor,
                   league_rate=league_rate, league_bf=league_bf,
                   dispersion=float(dispersion))

    def distribution(
        self, pitcher_id: str, opponent: str | None = None
    ) -> NegativeBinomial | None:
        mean = self.means.get(str(pitcher_id))
        if mean is None:
            return None
        factor = self.opp_factor.get(str(opponent), 1.0) if opponent else 1.0
        return NegativeBinomial(mean * factor, self.dispersion)

    def for_game(self, opponents: dict[str, str]) -> GameKProps:
        """A game-scoped distributions view with the opponent baked in.

        ``opponents`` maps pitcher id → the lineup he faces (batting team
        name), so the slate pricer's opponent-blind protocol still prices
        the matchup-adjusted mean.
        """
        return GameKProps(self, {str(k): v for k, v in opponents.items()})


@dataclass
class GameKProps:
    """The ``PropDistributions`` protocol over one game's probables."""

    model: PitcherKModel
    opponents: dict[str, str]

    def _dist(self, player_id: str) -> NegativeBinomial | None:
        pid = str(player_id)
        if pid not in self.opponents:
            return None
        return self.model.distribution(pid, opponent=self.opponents.get(pid))

    def has(self, player_id: str, market: str) -> bool:
        return market == MARKET and self._dist(player_id) is not None

    def prob_over(self, player_id: str, market: str, point: float) -> float:
        dist = self._dist(player_id)
        if dist is None or market != MARKET:
            return 0.5
        # P(X > point): exact for half-point lines; whole lines exclude the
        # push mass on both sides.
        return 1.0 - dist.cdf(int(point))

    def prob_under(self, player_id: str, market: str, point: float) -> float:
        dist = self._dist(player_id)
        if dist is None or market != MARKET:
            return 0.5
        threshold = int(point) if point != int(point) else int(point) - 1
        return dist.cdf(threshold)
