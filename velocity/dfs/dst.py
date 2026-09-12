"""A DraftKings DST projection — the pool's missing position.

DK's classic roster needs a defense, and the pool joined DST at 0.0 points:
the optimizer took the cheapest one and the lineup total ignored 5–15 points
of variance (docs/SYSTEM_REVIEW.md §6.1). Two sources price it:

* **Counting stats** — sacks, interceptions, fumble recoveries, defensive
  and return touchdowns, safeties — from the FantasyPros weekly DST
  projection (``def_sack`` … ``def_retd``), at DK's weights.
* **Points allowed** — DK's brackets (0 → +10 … 35+ → −4) are a function of
  the opponent's score, and the game sim already produces that distribution
  (``simulate_game`` from the run's ``mu_home`` / ``mu_away``), so the
  bracket expectation is read off the samples exactly. Without a game sim
  the fallback is FantasyPros' own bracket probabilities when the frame
  carries them, else the league's points-allowed distribution.

The same samples feed the GPP builder: a defense's per-sim points are its
counting stats drawn Poisson plus the bracket of that sim's opponent score,
so a DST is correlated with the game it plays in.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from velocity.dfs.scoring import _ID_COLUMNS

# DK classic DST scoring per unit of each FantasyPros DST stat. Forced
# fumbles (def_ff) are not scored; blocked kicks are not projected.
DK_DST_WEIGHTS: Mapping[str, float] = {
    "def_sack": 1.0,
    "def_int": 2.0,
    "def_fr": 2.0,
    "def_safety": 2.0,
    "def_td": 6.0,
    "def_retd": 6.0,
}

# Points-allowed brackets: (upper bound inclusive, points).
PA_BRACKETS: tuple[tuple[float, float], ...] = (
    (0, 10.0), (6, 7.0), (13, 4.0), (20, 1.0), (27, 0.0), (34, -1.0), (np.inf, -4.0),
)
# FantasyPros' bracket columns, in the same order.
FP_PA_COLUMNS = ("def_pa_a", "def_pa_b", "def_pa_c", "def_pa_d", "def_pa_e", "def_pa_f", "def_pa_g")
# The league's points-allowed distribution when neither the sim nor FP has
# one (2020–2025 team-games): mean ≈ 22.7 points.
LEAGUE_PA_PROBS = (0.011, 0.055, 0.168, 0.290, 0.255, 0.146, 0.075)

DST_POSITIONS = frozenset({"DST", "DEF", "D/ST", "D"})


def pa_points(points_allowed: object) -> np.ndarray:
    """DK bracket points for each points-allowed value."""
    pa = np.asarray(points_allowed, dtype=float)
    out = np.full(pa.shape, PA_BRACKETS[-1][1])
    for bound, pts in reversed(PA_BRACKETS[:-1]):
        out = np.where(pa <= bound, pts, out)
    return out


def bracket_expectation(probs: object) -> float:
    """E[bracket points] from bracket probabilities (renormalized)."""
    p = np.asarray(probs, dtype=float)
    if p.sum() <= 0:
        return 0.0
    p = p / p.sum()
    return float(sum(pi * pts for pi, (_bound, pts) in zip(p, PA_BRACKETS, strict=True)))


@dataclass(frozen=True)
class DstProjection:
    team: str
    name: str
    player_id: str
    counting: dict[str, float]  # FP stat → per-game mean
    pa_source: str  # "sim" / "fantasypros" / "league"
    pa_expected: float

    @property
    def points(self) -> float:
        counted = sum(DK_DST_WEIGHTS[k] * v for k, v in self.counting.items()
                      if k in DK_DST_WEIGHTS)
        return float(counted + self.pa_expected)


def dst_rows(fp: pd.DataFrame) -> pd.DataFrame:
    """The DST rows of a FantasyPros long frame, one wide row per team."""
    if fp.empty:
        return pd.DataFrame()
    pos = fp["position"].astype(str).str.upper()
    d = fp[pos.isin(DST_POSITIONS)].copy()
    if d.empty:
        return d
    d["stat"] = d["stat"].astype(str).str.lower()
    d["value"] = pd.to_numeric(d["value"], errors="coerce").fillna(0.0)
    ids = d.groupby("team").agg(player_id=("player_id", "first"),
                                player_name=("player_name", "first"))
    wide = d.pivot_table(index="team", columns="stat", values="value", aggfunc="mean")
    return ids.join(wide).reset_index()


def project_dst(
    fp: pd.DataFrame,
    opponent_scores: Mapping[str, np.ndarray] | None = None,
) -> list[DstProjection]:
    """One projection per DST in the FantasyPros frame.

    ``opponent_scores`` maps a defense's team code to per-sim points its
    opponent scores (the game sim); a team without one falls back to the
    frame's bracket probabilities, then the league distribution.
    """
    rows = dst_rows(fp)
    out: list[DstProjection] = []
    for r in rows.to_dict("records"):
        team = str(r["team"])
        counting = {k: float(r.get(k, 0.0) or 0.0) for k in DK_DST_WEIGHTS}
        scores = None if opponent_scores is None else opponent_scores.get(team)
        if scores is not None and len(scores):
            expected, source = float(pa_points(scores).mean()), "sim"
        else:
            probs = [float(r.get(c, 0.0) or 0.0) for c in FP_PA_COLUMNS]
            if sum(probs) > 0:
                expected, source = bracket_expectation(probs), "fantasypros"
            else:
                expected, source = bracket_expectation(LEAGUE_PA_PROBS), "league"
        out.append(DstProjection(
            team=team, name=str(r["player_name"]), player_id=str(r["player_id"]),
            counting=counting, pa_source=source, pa_expected=expected,
        ))
    return out


def dst_expected_points(
    fp: pd.DataFrame, opponent_scores: Mapping[str, np.ndarray] | None = None
) -> pd.DataFrame:
    """DST rows in the scorer's shape: ``[player_id, player_name, team, position, points]``."""
    projections = project_dst(fp, opponent_scores)
    if not projections:
        return pd.DataFrame(columns=[*_ID_COLUMNS, "points"])
    return pd.DataFrame([
        {"player_id": p.player_id, "player_name": p.name, "team": p.team,
         "position": "DST", "points": round(p.points, 2)}
        for p in projections
    ])[[*_ID_COLUMNS, "points"]]


# The realized counterpart of DK_DST_WEIGHTS, keyed by nflverse's TEAM-week
# column names. Every DK DST scoring event that nflverse counts is here: the
# turnovers and sacks, the defensive and return touchdowns, safeties, blocked
# kicks (2 apiece), and a returned two-point try. Points allowed is not a
# counting stat — it comes off the opponent's final score, through the same
# brackets the projection prices.
DK_DST_ACTUAL_WEIGHTS: Mapping[str, float] = {
    "def_sacks": 1.0,
    "def_interceptions": 2.0,
    "def_fumbles": 2.0,  # recoveries; forced fumbles are def_fumbles_forced
    "def_tds": 6.0,
    "special_teams_tds": 6.0,  # a kick or punt returned for a score
    "def_safeties": 2.0,
    "def_punt_blocks": 2.0,
    "def_pat_blocks": 2.0,
    "def_fg_blocks": 2.0,
    "def_2pt_made": 2.0,  # a two-point try returned the other way
}


def dst_actuals(team_weeks: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    """What each defense actually scored, from the nflverse team-week release.

    The grading counterpart of :func:`dst_expected_points`. A DST is a ninth
    of every classic roster, and the player-week release has no row for it —
    so without this a graded NFL lineup silently books its defense at zero and
    every entry reads as a miss by five to fifteen points.

    Returns ``[team, game_id, kickoff, actual]``: the counting stats at DK's
    weights plus the points-allowed bracket of the opponent's final score,
    which the schedule supplies. A week with no schedule row (an unplayed or
    unmatched game) is dropped rather than scored without its bracket.
    """
    columns = ["team", "game_id", "kickoff", "actual"]
    if team_weeks.empty or schedule.empty:
        return pd.DataFrame(columns=columns)
    games = schedule.copy()
    games["kickoff"] = pd.to_datetime(games["kickoff"], errors="coerce")
    # One row per SIDE: the team, its game, and what it gave up.
    sides = pd.concat(
        [
            games.assign(team=games["home_team"].astype(str),
                         points_allowed=pd.to_numeric(games["away_score"], errors="coerce")),
            games.assign(team=games["away_team"].astype(str),
                         points_allowed=pd.to_numeric(games["home_score"], errors="coerce")),
        ],
        ignore_index=True,
    )[["game_id", "season", "week", "kickoff", "team", "points_allowed"]]
    sides = sides.dropna(subset=["points_allowed", "kickoff"])

    # The release carries its own ``game_id`` and may carry a kickoff; the
    # schedule is the authority on both, and letting them collide renames the
    # columns out from under the result.
    weeks = team_weeks.drop(columns=["game_id", "kickoff", "points_allowed"],
                            errors="ignore").copy()
    weeks["team"] = weeks["team"].astype(str)
    for frame in (sides, weeks):
        for key in ("season", "week"):
            frame[key] = pd.to_numeric(frame[key], errors="coerce").astype("Int64")
    merged = weeks.merge(sides, on=["season", "week", "team"], how="inner")
    if merged.empty:
        return pd.DataFrame(columns=columns)

    counted = pd.Series(0.0, index=merged.index)
    for column, weight in DK_DST_ACTUAL_WEIGHTS.items():
        if column in merged.columns:
            counted += weight * pd.to_numeric(merged[column], errors="coerce").fillna(0.0)
    merged["actual"] = counted + pa_points(merged["points_allowed"])
    return merged[columns].reset_index(drop=True)


# Team codes DraftKings and nflverse spell differently, plus the relocations
# that still appear in older seasons. Every spelling of a club points at the
# same defense, so registering them all only ever adds a key — it can never
# join two clubs together. The live divergence today is the Rams: DK writes
# "LAR" and the nflverse releases write "LA".
TEAM_CODE_ALIASES: tuple[tuple[str, ...], ...] = (
    ("LA", "LAR", "STL"),
    ("LAC", "SD"),
    ("LV", "OAK"),
    ("JAX", "JAC"),
    ("WAS", "WSH", "WFT"),
    ("ARI", "ARZ"),
    ("BAL", "BLT"),
    ("CLE", "CLV"),
    ("HOU", "HST"),
)


def dst_day_index(actuals: pd.DataFrame) -> dict[tuple[str, Any], dict[str, Any]]:
    """(team code, game date) → the defense's realized DK points.

    Keyed under every spelling of the club (:data:`TEAM_CODE_ALIASES`), which
    is what lets a DK board's ``LAR`` find an nflverse ``LA``. The date is the
    game's own, so a Thursday defense is never graded against Sunday's.
    """
    from velocity.dfs.backtest import norm

    spellings = {norm(code): group for group in TEAM_CODE_ALIASES for code in group}
    index: dict[tuple[str, Any], dict[str, Any]] = {}
    for row in actuals.to_dict("records"):
        when = pd.Timestamp(row["kickoff"])
        if pd.isna(when):
            continue
        day = when.normalize().date()
        record = {"actual": float(row["actual"]), "game_id": str(row["game_id"]),
                  "team": str(row["team"])}
        for code in spellings.get(norm(row["team"]), (str(row["team"]),)):
            index[(norm(code), day)] = record
    return index


def dst_samples(
    projections: list[DstProjection],
    opponent_scores: Mapping[str, np.ndarray],
    rng: np.random.Generator,
    n_sims: int,
) -> dict[str, np.ndarray]:
    """Per-sim DK points per DST name, correlated with the game through the
    opponent's simulated score; counting stats are Poisson on their means."""
    out: dict[str, np.ndarray] = {}
    for p in projections:
        scores = opponent_scores.get(p.team)
        if scores is None or not len(scores):
            continue
        idx = rng.integers(0, len(scores), size=n_sims)
        total = pa_points(scores[idx])
        for stat, weight in DK_DST_WEIGHTS.items():
            mean = p.counting.get(stat, 0.0)
            if mean > 0:
                total = total + weight * rng.poisson(mean, n_sims)
        out[p.name] = total.astype(float)
    return out


def opponent_scores_from_projections(
    projections: pd.DataFrame, n_sims: int, rng: np.random.Generator,
    config: object | None = None,
) -> dict[str, np.ndarray]:
    """``defense team → opponent's per-sim score`` from a run's projections frame.

    ``projections`` is the live run's ``projections_{league}_*.parquet``
    (``home`` / ``away`` / ``mu_home`` / ``mu_away``); each game is
    re-simulated with the football sim so the brackets come off the same
    distribution the board was priced from.
    """
    from velocity.models.simulate import SimConfig, simulate_game

    out: dict[str, np.ndarray] = {}
    if projections.empty:
        return out
    cfg = config or SimConfig(n_sims=n_sims)
    for r in projections.drop_duplicates("game_id").to_dict("records"):
        mu_home, mu_away = float(r["mu_home"]), float(r["mu_away"])
        sim = simulate_game(mu_home - mu_away, mu_home + mu_away, rng, cfg)  # type: ignore[arg-type]
        out[str(r["home"])] = sim.away_score  # the home defense allows the away score
        out[str(r["away"])] = sim.home_score
    return out
