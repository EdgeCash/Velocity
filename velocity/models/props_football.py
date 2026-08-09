"""Football prop model — FantasyPros consensus → correlated per-game distributions.

Props decompose as ``team volume × player share × efficiency`` (DESIGN §5).
FantasyPros consensus projections already encode the share and efficiency
terms per player (they are point projections); what they lack is a
**distribution** and **correlation**. This module supplies both:

* Each team draws one **pass-volume multiplier** and one **rush-volume
  multiplier** per simulation (lognormal, mean 1). Every player's outcome is
  conditioned on their team's draws, so teammates move together — a big
  passing day lifts the QB and every receiver at once.
* A QB's passing yards are the **sum of his receivers' simulated receiving
  yards** (scaled to his projection), so QB-over + WR1-over parlays price
  their true correlation, not a naive product.
* Receptions are Poisson on the multiplied mean (Poisson × lognormal volume =
  overdispersed marginally, as counts should be); receiving yards ride the
  simulated receptions with per-catch noise; rushing yards are normal around
  the multiplied mean; TDs are Poisson, so ``anytime_td`` is P(count ≥ 1).

The result satisfies :class:`~velocity.wagering.props_slate.PropDistributions`
(pricing) and exposes ``player_samples`` (the parlay protocol's prop hook).

Dispersion defaults below are honest priors, not fitted constants — the prop
backtest's shrink sweep (docs/FOOTBALL_CUTOVER.md Phase 3) is what calibrates
the confidence actually bet on, exactly as it did for MLB.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# FantasyPros stat keys → the canonical prop market they feed. TD components
# fold into the anytime-TD rate rather than being markets of their own.
FP_STAT_TO_MARKET = {
    "pass_yds": "pass_yards",
    "pass_tds": "pass_tds",
    "rush_yds": "rush_yards",
    "rec_yds": "receiving_yards",
    "rec": "receptions",
    "receptions": "receptions",
}
_TD_STATS = ("rush_tds", "rec_tds")

# Below this projected stat volume a player is noise, not a prop — skip them.
_MIN_MEAN = {"pass_yards": 25.0, "pass_tds": 0.05, "rush_yards": 5.0,
             "receiving_yards": 5.0, "receptions": 0.5, "anytime_td": 0.02}


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def player_key(player_id: object, player_name: object) -> str:
    """The sim's player id: the provider id when present, else the normalized name."""
    if player_id is not None and pd.notna(player_id) and str(player_id):  # type: ignore[call-overload]
        return str(player_id)
    return _normalize_name(str(player_name))


@dataclass(frozen=True)
class FootballPropConfig:
    """Simulation size and dispersion priors (calibrated later by the backtest)."""

    n_sims: int = 10_000
    # Team-level volume swing (lognormal sigma): how much a game script moves
    # the whole passing/rushing pie versus its projection.
    pass_volume_sigma: float = 0.18
    rush_volume_sigma: float = 0.25
    # Per-reception yardage noise, scaled by sqrt(receptions).
    yards_sd_per_reception: float = 6.0
    # Player rushing-yards noise on top of the team multiplier, as a fraction
    # of the player's mean (floored so goal-line backs aren't near-determinate).
    rush_sd_fraction: float = 0.45
    rush_sd_floor: float = 8.0


class FootballPropSim:
    """Correlated per-simulation player outcomes for one game, with pricing.

    ``samples`` maps ``(player_key, market) → np.ndarray`` of per-sim values.
    Satisfies the prop-slate ``PropDistributions`` protocol and the parlay
    engine's ``player_samples`` hook.
    """

    def __init__(self, samples: dict[tuple[str, str], np.ndarray]) -> None:
        self.samples = samples

    def has(self, player_id: str, market: str) -> bool:
        return (player_id, market) in self.samples

    def player_samples(self, player: str, market: str) -> np.ndarray:
        return self.samples[(player, market)]

    def prob_over(self, player_id: str, market: str, point: float) -> float:
        return float(np.mean(self.samples[(player_id, market)] > point))

    def prob_under(self, player_id: str, market: str, point: float) -> float:
        return float(np.mean(self.samples[(player_id, market)] < point))

    def mean(self, player_id: str, market: str) -> float:
        return float(np.mean(self.samples[(player_id, market)]))


@dataclass
class _PlayerMeans:
    key: str
    name: str
    position: str
    means: dict[str, float] = field(default_factory=dict)
    td_rate: float = 0.0


def team_player_means(fp: pd.DataFrame, team: str) -> list[_PlayerMeans]:
    """Collapse one team's long FantasyPros frame into per-player market means."""
    rows = fp[fp["team"].astype(str) == str(team)]
    out: dict[str, _PlayerMeans] = {}
    for r in rows.to_dict("records"):
        key = player_key(r.get("player_id"), r.get("player_name"))
        entry = out.get(key)
        if entry is None:
            entry = _PlayerMeans(key=key, name=str(r.get("player_name")),
                                 position=str(r.get("position") or ""))
            out[key] = entry
        stat = str(r.get("stat"))
        value = float(r.get("value") or 0.0)
        if stat in FP_STAT_TO_MARKET:
            entry.means[FP_STAT_TO_MARKET[stat]] = value
        elif stat in _TD_STATS:
            entry.td_rate += value
    return list(out.values())


def simulate_team_props(
    players: list[_PlayerMeans],
    rng: np.random.Generator,
    config: FootballPropConfig | None = None,
) -> dict[tuple[str, str], np.ndarray]:
    """Simulate one team's player outcomes under shared volume multipliers."""
    config = config or FootballPropConfig()
    n = config.n_sims
    # Lognormal with mean exactly 1 (mu = -sigma^2/2), so projections are the
    # distribution's mean, not its median.
    pass_mult = rng.lognormal(-config.pass_volume_sigma**2 / 2, config.pass_volume_sigma, n)
    rush_mult = rng.lognormal(-config.rush_volume_sigma**2 / 2, config.rush_volume_sigma, n)

    samples: dict[tuple[str, str], np.ndarray] = {}
    receiving_yards_total = np.zeros(n)

    for p in players:
        rec_mean = p.means.get("receptions", 0.0)
        rec_yds_mean = p.means.get("receiving_yards", 0.0)
        if rec_mean >= _MIN_MEAN["receptions"]:
            receptions = rng.poisson(rec_mean * pass_mult).astype(float)
            samples[(p.key, "receptions")] = receptions
            ypr = (rec_yds_mean / rec_mean) if rec_mean > 0 else 10.0
            noise = rng.normal(0.0, config.yards_sd_per_reception * np.sqrt(receptions))
            rec_yards = np.clip(receptions * ypr + noise, 0.0, None)
            if rec_yds_mean >= _MIN_MEAN["receiving_yards"]:
                samples[(p.key, "receiving_yards")] = rec_yards
            receiving_yards_total += rec_yards

        rush_mean = p.means.get("rush_yards", 0.0)
        if rush_mean >= _MIN_MEAN["rush_yards"]:
            sd = max(config.rush_sd_fraction * rush_mean, config.rush_sd_floor)
            rush = rng.normal(rush_mean * rush_mult, sd)
            samples[(p.key, "rush_yards")] = np.clip(rush, 0.0, None)

        pass_tds_mean = p.means.get("pass_tds", 0.0)
        if pass_tds_mean >= _MIN_MEAN["pass_tds"]:
            samples[(p.key, "pass_tds")] = rng.poisson(pass_tds_mean * pass_mult).astype(float)

        if p.td_rate >= _MIN_MEAN["anytime_td"]:
            # Scoring rides the game script too: blend of the two multipliers.
            script = 0.5 * (pass_mult + rush_mult)
            samples[(p.key, "anytime_td")] = rng.poisson(p.td_rate * script).astype(float)

    # QB passing yards = the team's simulated receiving yards, scaled onto each
    # passer's own projection — exact correlation with his receivers by
    # construction. (Multiple projected passers split the same team total.)
    team_pass_mean = sum(
        p.means.get("pass_yards", 0.0)
        for p in players
        if p.means.get("pass_yards", 0.0) >= _MIN_MEAN["pass_yards"]
    )
    if team_pass_mean > 0 and receiving_yards_total.mean() > 0:
        realized_mean = float(receiving_yards_total.mean())
        for p in players:
            pass_mean = p.means.get("pass_yards", 0.0)
            if pass_mean < _MIN_MEAN["pass_yards"]:
                continue
            scale = pass_mean / realized_mean
            samples[(p.key, "pass_yards")] = receiving_yards_total * scale
    return samples


def game_props(
    fp: pd.DataFrame,
    home_team: str,
    away_team: str,
    rng: np.random.Generator,
    config: FootballPropConfig | None = None,
) -> FootballPropSim:
    """Simulate both teams' player props for one game (teams independent)."""
    samples: dict[tuple[str, str], np.ndarray] = {}
    for team in (home_team, away_team):
        samples.update(simulate_team_props(team_player_means(fp, team), rng, config))
    return FootballPropSim(samples)


def name_index_from_fp(fp: pd.DataFrame) -> dict[str, str]:
    """Normalized player name → sim player key, for resolving provider prop names."""
    out: dict[str, str] = {}
    for r in fp.drop_duplicates(subset=["player_name"]).to_dict("records"):
        name = r.get("player_name")
        if name is None or pd.isna(name):
            continue
        out[_normalize_name(str(name))] = player_key(r.get("player_id"), name)
    return out
