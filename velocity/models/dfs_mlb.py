"""Contextual MLB DFS projections — season rates ADJUSTED for the matchup.

The model this replaces priced every hitter at his flat season rate: a bat
facing a Cy Young contender in Oracle Park scored the same as one facing a
call-up in Sacramento, and a nine-hole hitter the same as a leadoff man. On
the first live slate our projections correlated +0.03 with actual DK points
among hitters — indistinguishable from noise, and structurally so, because
the projection could not see any of the three things that most move a bat's
night.

The decomposition mirrors the home-run model (velocity/models/props_hr.py),
which already validated these exact context terms:

    hitter DK  = dk_per_pa(batter) x pitcher_factor x park_factor x E[PA|slot]
    pitcher DK = dk_per_start(pitcher) x offense_factor x park_factor

Every term is fit from the banked box scores with empirical-Bayes shrinkage;
nothing is imported from a paper. A player, pitcher, park, or slot the bank
has never seen falls back to the league mean rather than guessing.

Pure functions of frames; offline-testable, no network.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

# DraftKings MLB classic scoring.
HITTER_POINTS = {"single": 3.0, "double": 5.0, "triple": 8.0, "hr": 10.0,
                 "rbi": 2.0, "r": 2.0, "bb": 2.0, "hbp": 2.0, "sb": 5.0}
PITCHER_POINTS = {"ip": 2.25, "k": 2.0, "win": 4.0, "er": -2.0,
                  "hits_allowed": -0.6, "bb": -0.6, "hbp": -0.6}

# Empirical-Bayes prior strengths, in each rate's own unit. Context terms are
# noisier than the player rate itself, so they shrink harder.
BATTER_PRIOR_PA = 250.0
PITCHER_PRIOR_STARTS = 8.0
OPPOSING_PITCHER_PRIOR_BF = 400.0
OPPOSING_OFFENSE_PRIOR_PA = 2000.0
PARK_PRIOR_PA = 6000.0
# Multiplier clamps — context states a lean, never runs away on thin samples.
PITCHER_CLAMP = (0.75, 1.30)
OFFENSE_CLAMP = (0.75, 1.30)
PARK_CLAMP = (0.85, 1.20)


def hitter_dk_points(frame: pd.DataFrame) -> pd.Series:
    """DK points scored, per banked batter-game row."""
    def col(name: str) -> pd.Series:
        if name not in frame.columns:
            return pd.Series(0.0, index=frame.index)
        return pd.to_numeric(frame[name], errors="coerce").fillna(0.0)

    singles = col("h") - col("double") - col("triple") - col("hr")
    return (singles.clip(lower=0) * HITTER_POINTS["single"]
            + col("double") * HITTER_POINTS["double"]
            + col("triple") * HITTER_POINTS["triple"]
            + col("hr") * HITTER_POINTS["hr"]
            + col("rbi") * HITTER_POINTS["rbi"]
            + col("r") * HITTER_POINTS["r"]
            + col("bb") * HITTER_POINTS["bb"]
            + col("hbp") * HITTER_POINTS["hbp"]
            + col("sb") * HITTER_POINTS["sb"])


def pitcher_dk_points(frame: pd.DataFrame) -> pd.Series:
    """DK points scored, per banked starter-game row."""
    def col(name: str) -> pd.Series:
        if name not in frame.columns:
            return pd.Series(0.0, index=frame.index)
        return pd.to_numeric(frame[name], errors="coerce").fillna(0.0)

    innings = col("outs") / 3.0
    return (innings * PITCHER_POINTS["ip"]
            + col("k") * PITCHER_POINTS["k"]
            + col("win") * PITCHER_POINTS["win"]
            + col("er") * PITCHER_POINTS["er"]
            + col("hits_allowed") * PITCHER_POINTS["hits_allowed"]
            + col("bb") * PITCHER_POINTS["bb"]
            + col("hbp") * PITCHER_POINTS["hbp"])


# Columns the game join supplies. A caller frame that already carries them
# (the backtest harness does) must give them up first, or pandas suffixes
# both copies and the fit reads neither.
_JOINED = ["home_team", "away_team", "kickoff"]


def _shrink(total: pd.Series, exposure: pd.Series, league: float,
            prior: float) -> pd.Series:
    """Empirical-Bayes rate: observed blended toward league by prior weight."""
    return (total + league * prior) / (exposure + prior)


def _recency_weights(frame: pd.DataFrame, half_life: float | None) -> pd.Series:
    """Per-row exponential recency weight, or all ones when disabled.

    ``half_life`` is in days and is measured against the newest game in the
    fit window, so the weights mean the same thing whichever window a
    walk-forward pass is on. A frame with no usable dates weighs everything
    equally rather than inventing an ordering.
    """
    ones = pd.Series(1.0, index=frame.index)
    if not half_life or half_life <= 0 or "kickoff" not in frame.columns:
        return ones
    stamps = pd.to_datetime(frame["kickoff"], errors="coerce")
    if stamps.notna().sum() == 0:
        return ones
    age_days = (stamps.max() - stamps).dt.total_seconds() / 86_400.0
    return pd.Series(0.5 ** (age_days / half_life), index=frame.index).fillna(1.0)


@dataclass(frozen=True)
class DfsMlbModel:
    """Fitted DK-point rates and context multipliers."""

    league_hitter_rate: float   # DK points per plate appearance
    league_pitcher_rate: float  # DK points per start
    batter_rate: Mapping[str, float]
    pitcher_rate: Mapping[str, float]
    pitcher_factor: Mapping[str, float]   # opposing SP → hitter multiplier
    offense_factor: Mapping[str, float]   # opposing lineup → pitcher multiplier
    park_factor: Mapping[str, float]
    slot_pa: Mapping[int, float]
    default_pa: float = 4.0

    def project_hitter(
        self, batter_id: str, *, opposing_starter: str | None = None,
        venue: str | None = None, lineup_slot: int | None = None,
    ) -> float | None:
        """Expected DK points for one hitter in one game."""
        rate = self.batter_rate.get(str(batter_id))
        if rate is None:
            return None
        if opposing_starter is not None:
            rate *= self.pitcher_factor.get(str(opposing_starter), 1.0)
        if venue is not None:
            rate *= self.park_factor.get(str(venue), 1.0)
        pa = (self.slot_pa.get(int(lineup_slot), self.default_pa)
              if lineup_slot else self.default_pa)
        return float(rate * pa)

    def project_pitcher(
        self, pitcher_id: str, *, opposing_team: str | None = None,
        venue: str | None = None, use_context: bool = False,
    ) -> float | None:
        """Expected DK points for one starting pitcher.

        Context is **off by default, on evidence**. Walk-forward over 146
        slates (docs/DFS_MODEL.md): context made pitcher projections WORSE —
        mean within-slate correlation 0.2656 against 0.2730 flat, better on
        only 45% of slates. The opposing-offense and park terms below are
        crude (season-long team rates; an inverted park factor rather than a
        fitted pitcher-park effect) and a starter's own rate already absorbs
        much of what they try to add. The capability stays so a properly
        fitted replacement can be switched on when it earns it — a flat
        pitcher rate is the better projection today.
        """
        rate = self.pitcher_rate.get(str(pitcher_id))
        if rate is None:
            return None
        if not use_context:
            return float(rate)
        if opposing_team is not None:
            rate *= self.offense_factor.get(str(opposing_team), 1.0)
        if venue is not None:
            # A hitter's park helps hitters, so it HURTS the pitcher: the
            # multiplier inverts rather than applying twice in one direction.
            park = self.park_factor.get(str(venue), 1.0)
            rate *= (2.0 - park) if park else 1.0
        return float(rate)

    @classmethod
    def fit(
        cls, batters: pd.DataFrame, starters: pd.DataFrame, games: pd.DataFrame,
        *, season: int | None = None, pitcher_half_life: float | None = None,
    ) -> DfsMlbModel:
        """Fit every rate and context term from the banked box scores.

        ``pitcher_half_life`` (in days) weights a starter's own history by
        recency: a start ``h`` days old counts half as much as today's.
        **Off by default, on evidence.** It was built to attack the ~9%
        level bias the lineup backtests found in pitcher projections, as a
        candidate that changes rankings rather than levels — and it fails
        monotonically (docs/DFS_MODEL.md §4): over 12,936 starts, mean
        within-slate correlation runs +0.2682 flat, +0.2566 at a 45-day
        half-life, +0.2297 at 14 days, with each slate's top-2 arms falling
        the same way. It does shrink the level bias (+0.62 to +0.22), which
        is exactly the shape of the per-class rescale the showdown backtest
        also rejected: both buy calibration by discarding sample. A starting
        pitcher's recent form is mostly noise. The knob stays so the negative
        result is executable rather than a paragraph.
        """
        games = games.copy()
        games["game_id"] = games["game_id"].astype(str)
        if season is not None and "season" in games.columns:
            games = games[games["season"] == season]
        columns = ["game_id", "home_team", "away_team"]
        if "kickoff" in games.columns:
            columns.append("kickoff")
        keys = games[columns]

        bat = batters.copy()
        bat["game_id"] = bat["game_id"].astype(str)
        bat["batter_id"] = bat["batter_id"].astype(str)
        # Fit owns the game join: a caller frame that already carries these
        # columns would otherwise merge into _x/_y and silently lose them.
        bat = bat.drop(columns=_JOINED, errors="ignore")
        bat = bat.merge(keys, on="game_id", how="inner")
        bat["pa"] = pd.to_numeric(bat["pa"], errors="coerce")
        bat = bat[bat["pa"] > 0]
        if bat.empty:
            return cls(0.0, 0.0, {}, {}, {}, {}, {}, {})
        bat["dk"] = hitter_dk_points(bat)

        sp = starters.copy()
        sp["game_id"] = sp["game_id"].astype(str)
        sp["starter_id"] = sp["starter_id"].astype(str)
        sp = sp.drop(columns=_JOINED, errors="ignore")
        sp = sp.merge(keys, on="game_id", how="inner")
        sp["dk"] = pitcher_dk_points(sp)

        league_h = float(bat["dk"].sum() / bat["pa"].sum())
        league_p = float(sp["dk"].mean()) if not sp.empty else 0.0

        by_batter = bat.groupby("batter_id").agg(dk=("dk", "sum"), pa=("pa", "sum"))
        batter_rate = _shrink(by_batter["dk"], by_batter["pa"], league_h,
                              BATTER_PRIOR_PA)

        sp["weight"] = _recency_weights(sp, pitcher_half_life)
        by_pitcher = sp.assign(weighted=sp["dk"] * sp["weight"]).groupby(
            "starter_id").agg(dk=("weighted", "sum"), starts=("weight", "sum"))
        pitcher_rate = _shrink(by_pitcher["dk"], by_pitcher["starts"], league_p,
                               PITCHER_PRIOR_STARTS)

        # Opposing-pitcher effect on hitters: DK points allowed per batter
        # faced, relative to league, from the starter's own line.
        opp = sp.copy()
        if "batters_faced" in opp.columns:
            opp["bf"] = pd.to_numeric(opp["batters_faced"], errors="coerce")
            opp = opp[opp["bf"] > 0]
        pitcher_factor: dict[str, float] = {}
        if not opp.empty:
            # A pitcher's own DK points run OPPOSITE to the offense he allows,
            # so invert his rate into an offense-allowed multiplier.
            grouped = opp.groupby("starter_id").agg(dk=("dk", "sum"),
                                                    starts=("game_id", "size"))
            rate = _shrink(grouped["dk"], grouped["starts"], league_p,
                           PITCHER_PRIOR_STARTS)
            ratio = (2.0 - (rate / league_p)) if league_p else rate * 0 + 1.0
            ratio = ratio.clip(*PITCHER_CLAMP)
            pitcher_factor = {str(k): float(v) for k, v in ratio.items()}

        # Opposing-offense effect on pitchers: how much DK production a
        # lineup generates per plate appearance, relative to league.
        bat["batting_team"] = bat["away_team"].where(
            bat["side"].astype(str) == "away", bat["home_team"])
        by_offense = bat.groupby("batting_team").agg(dk=("dk", "sum"),
                                                     pa=("pa", "sum"))
        off_rate = _shrink(by_offense["dk"], by_offense["pa"], league_h,
                           OPPOSING_OFFENSE_PRIOR_PA) / league_h
        # A strong offense LOWERS the opposing pitcher's projection.
        offense_factor = (2.0 - off_rate).clip(*OFFENSE_CLAMP)

        by_park = bat.groupby("home_team").agg(dk=("dk", "sum"), pa=("pa", "sum"))
        park = _shrink(by_park["dk"], by_park["pa"], league_h,
                       PARK_PRIOR_PA) / league_h
        park = park.clip(*PARK_CLAMP)

        starting = bat[bat["started"].astype(bool)] if "started" in bat else bat
        starting = starting[starting["lineup_slot"].astype(int) > 0]
        slot_pa = (starting.groupby(starting["lineup_slot"].astype(int))["pa"]
                   .mean().to_dict() if not starting.empty else {})

        return cls(
            league_hitter_rate=league_h,
            league_pitcher_rate=league_p,
            batter_rate={str(k): float(v) for k, v in batter_rate.items()},
            pitcher_rate={str(k): float(v) for k, v in pitcher_rate.items()},
            pitcher_factor=pitcher_factor,
            offense_factor={str(k): float(v) for k, v in offense_factor.items()},
            park_factor={str(k): float(v) for k, v in park.items()},
            slot_pa={int(str(k)): float(v) for k, v in slot_pa.items()},
            default_pa=float(starting["pa"].mean()) if not starting.empty else 4.0,
        )
