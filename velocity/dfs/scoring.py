"""DFS scoring — FantasyPros projections → expected DraftKings points.

The DFS layer's projection input is the same consensus long frame the prop
model consumes (``velocity.ingest.fantasypros``). Scoring is DK classic NFL:
full-PPR, 4-point passing TDs, −1 interceptions and fumbles. Yardage
milestone bonuses (+3 at 300 pass / 100 rush / 100 rec) are deliberately NOT
applied to point projections — a bonus is a tail event, and adding it at the
mean overstates every projection; the sim-based DFS scoring (which prices the
bonus probability correctly) replaces this linear pass later
(docs/FOOTBALL_CUTOVER.md §5b).

Pure functions of frames; offline-testable.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

# FantasyPros stat key → DK classic points per unit.
DK_POINTS_PER_STAT = {
    "pass_yds": 0.04,
    "pass_tds": 4.0,
    "pass_int": -1.0,
    "pass_ints": -1.0,  # spelling drift tolerated
    "rush_yds": 0.1,
    "rush_tds": 6.0,
    "rec": 1.0,  # full PPR
    "receptions": 1.0,
    "rec_rec": 1.0,  # the live feed's spelling — a receiver's PPR points hung on it
    "rec_yds": 0.1,
    "rec_tds": 6.0,
    "fumbles": -1.0,
    "fumbles_lost": -1.0,
}

_ID_COLUMNS = ["player_id", "player_name", "team", "position"]

# --- MLB classic ---------------------------------------------------------------
# The FantasyPros MLB feed serves SEASON-TOTAL projections, so the scorer
# normalizes to per-game rates before applying DK weights: hitters by games
# (``g``), pitchers by starts (``gs``, falling back to ``g``). Stat keys are
# alias-tolerant like the FP normalizer itself — the exact live spelling is
# confirmed by the collector's --inspect run.

# Hitter stats → DK points per unit. Singles aren't published directly, so
# hits score at the single's weight and the extra-base columns top up the
# difference (2B +5 = 3 + 2, 3B +8 = 3 + 5, HR +10 = 3 + 7).
_MLB_HITTER_WEIGHTS = {
    ("h", "hits"): 3.0,
    ("2b", "doubles"): 2.0,
    ("3b", "triples"): 5.0,
    ("hr", "home_runs", "homeruns"): 7.0,
    ("rbi", "rbis"): 2.0,
    ("r", "runs"): 2.0,
    ("bb", "walks"): 2.0,
    ("hbp",): 2.0,
    ("sb", "stolen_bases"): 5.0,
}
# Pitcher stats → DK points per unit (IP +2.25, K +2, W +4, ER −2; the hits/
# walks-against −0.6 terms need "allowed" columns FP may not publish — absent
# keys simply contribute nothing).
_MLB_PITCHER_WEIGHTS = {
    ("ip", "innings", "innings_pitched"): 2.25,
    ("k", "so", "strikeouts"): 2.0,
    ("w", "wins"): 4.0,
    ("er", "earned_runs"): -2.0,
    ("h", "hits", "ha", "hits_allowed"): -0.6,
    ("bb", "walks", "bba", "walks_allowed"): -0.6,
}
_PITCHER_POSITIONS = frozenset({"P", "SP", "RP"})


def _wide(fp: pd.DataFrame) -> pd.DataFrame:
    """Pivot the FP long frame to one row per player with lowercase stat columns."""
    df = fp.copy()
    df["stat"] = df["stat"].astype(str).str.lower().str.strip()
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0.0)
    ids = (
        df.groupby("player_name", dropna=True)
        .agg(player_id=("player_id", "first"), team=("team", "first"),
             position=("position", "first"))
    )
    stats = df.pivot_table(index="player_name", columns="stat", values="value",
                           aggfunc="mean")
    return ids.join(stats, rsuffix="_stat").reset_index()


def _dot(frame: pd.DataFrame, weights: dict[tuple[str, ...], float]) -> pd.Series:
    """Σ weight × first-present-alias column, on the pivoted frame."""
    total = pd.Series(0.0, index=frame.index)
    for aliases, weight in weights.items():
        col = next((a for a in aliases if a in frame.columns), None)
        if col is not None:
            total += frame[col].fillna(0.0) * weight
    return total


# Empirical-Bayes games priors for the per-game rate: a call-up's two hot
# games must not out-project an everyday player's 130 (proven live — the
# first raw-rate solve rostered a one-game outfielder and priced a reliever's
# whole season against his single spot start). Shrunk toward the league's
# per-game mean for the player's class.
MLB_SHRINK_GAMES_HITTER = 15.0
MLB_SHRINK_GAMES_PITCHER = 5.0


def dk_expected_points_mlb(fp: pd.DataFrame) -> pd.DataFrame:
    """MLB season-total stats → expected DK points PER GAME, shrunken.

    Returns the same shape as :func:`dk_expected_points`. Works on any long
    frame with season totals (the statsapi snapshot, or a FantasyPros-shaped
    projections frame). Everyone divides by APPEARANCES: for a true starter
    games ≈ starts so the per-start rate is unchanged, while a reliever's
    per-outing value is honestly small instead of his whole season divided
    by one spot start. Rates shrink toward the league per-game mean of the
    player's class with a games prior, so thin samples price conservatively.
    Players without a games denominator are dropped.
    """
    if fp.empty:
        return pd.DataFrame(columns=[*_ID_COLUMNS, "points"])
    wide = _wide(fp)
    position = wide["position"].astype(str).str.upper().str.strip()
    is_pitcher = position.str.split("/").str[0].isin(_PITCHER_POSITIONS)
    # Earned runs may arrive only as a rate — derive the total from ERA × IP.
    if "er" not in wide.columns and {"era", "ip"}.issubset(wide.columns):
        wide["er"] = (pd.to_numeric(wide["era"], errors="coerce")
                      * pd.to_numeric(wide["ip"], errors="coerce") / 9.0)

    def column(*names: str) -> pd.Series:
        for name in names:
            if name in wide.columns:
                return pd.to_numeric(wide[name], errors="coerce")
        return pd.Series(float("nan"), index=wide.index)

    games = column("g", "games")
    season_points = _dot(wide, _MLB_HITTER_WEIGHTS).where(
        ~is_pitcher, _dot(wide, _MLB_PITCHER_WEIGHTS))
    denom = games.where(games > 0)

    def class_prior(mask: pd.Series) -> float:
        pts = season_points[mask & denom.notna()].sum()
        gms = denom[mask].sum()
        return float(pts / gms) if gms and gms > 0 else 0.0

    prior = pd.Series(class_prior(~is_pitcher), index=wide.index).where(
        ~is_pitcher, class_prior(is_pitcher))
    k = pd.Series(MLB_SHRINK_GAMES_HITTER, index=wide.index).where(
        ~is_pitcher, MLB_SHRINK_GAMES_PITCHER)
    per_game = (season_points + prior * k) / (denom + k)

    out = wide[["player_name", "player_id", "team", "position"]].assign(
        points=per_game.round(2))
    out = out.dropna(subset=["points"]).reset_index(drop=True)
    return out[[*_ID_COLUMNS, "points"]]


def dk_expected_points(fp: pd.DataFrame) -> pd.DataFrame:
    """Collapse a FantasyPros long frame into expected DK points per player.

    Returns ``[player_id, player_name, team, position, points]`` — one row per
    player, points = Σ stat-mean × DK weight over the stats DK scores. Players
    whose projected stats are all unscored (or zero) still appear with 0.0, so
    the optimizer can see cheap punts.
    """
    if fp.empty:
        return pd.DataFrame(columns=[*_ID_COLUMNS, "points"])
    df = fp.copy()
    df["dk"] = df["stat"].map(DK_POINTS_PER_STAT).fillna(0.0) * pd.to_numeric(
        df["value"], errors="coerce"
    ).fillna(0.0)
    grouped = (
        df.groupby("player_name", dropna=True)
        .agg(
            player_id=("player_id", "first"),
            team=("team", "first"),
            position=("position", "first"),
            points=("dk", "sum"),
        )
        .reset_index()
    )
    grouped["points"] = grouped["points"].round(2)
    return grouped[[*_ID_COLUMNS, "points"]]


def dk_expected_points_mlb_contextual(
    batters: pd.DataFrame,
    starters: pd.DataFrame,
    games: pd.DataFrame,
    *,
    opposing_starter: dict[str, str] | None = None,
    venue_of_team: dict[str, str] | None = None,
    lineup_slot: dict[str, int] | None = None,
    eligible_batters: set[str] | None = None,
    season: int | None = None,
) -> pd.DataFrame:
    """MLB DK projections WITH matchup context (velocity/models/dfs_mlb.py).

    Returns the same ``[player_id, player_name, team, position, points]``
    shape the flat scorer emits, so the optimizer consumes it unchanged.
    Hitters carry the opposing-starter, park and lineup-slot terms; pitchers
    are flat by evidence (docs/DFS_MODEL.md §4).

    ``opposing_starter`` maps batter id → the starter he faces today,
    ``venue_of_team`` team → the park, ``lineup_slot`` batter id → his slot.
    Anything absent simply drops that term for that player.

    ``eligible_batters`` restricts the hitter pool to the bats announced to
    start. It matters more than any context term: measured on the showdown
    backtest, a roster built before lineups post carries 2.1 players who
    never appear out of six, and the same optimizer fed the confirmed card
    scores about 11 DK points more. Pass ``None`` (lineups not yet posted)
    and every banked batter is priced, as before.
    """
    from velocity.models.dfs_mlb import DfsMlbModel

    model = DfsMlbModel.fit(batters, starters, games, season=season)
    opposing = dict(opposing_starter or {})
    venues = dict(venue_of_team or {})
    slots = dict(lineup_slot or {})

    # Identity comes from the banks: batters from the batter frame, starters
    # from the starter frame, so the join key stays the MLBAM player id.
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    if not batters.empty:
        recent = batters.drop_duplicates("batter_id", keep="last")
        for row in recent.to_dict("records"):
            pid = str(row["batter_id"])
            if eligible_batters is not None and pid not in eligible_batters:
                continue
            team = str(row.get("team") or "")
            points = model.project_hitter(
                pid, opposing_starter=opposing.get(pid),
                venue=venues.get(team), lineup_slot=slots.get(pid))
            if points is None:
                continue
            seen.add(pid)
            rows.append({"player_id": pid,
                         "player_name": str(row.get("batter_name") or pid),
                         "team": team, "position": None,
                         "points": round(points, 2)})
    if starters is not None and not starters.empty:
        recent_sp = starters.drop_duplicates("starter_id", keep="last")
        for row in recent_sp.to_dict("records"):
            pid = str(row["starter_id"])
            if pid in seen:
                continue
            points = model.project_pitcher(pid)
            if points is None:
                continue
            rows.append({"player_id": pid,
                         "player_name": str(row.get("starter_name") or pid),
                         "team": str(row.get("team") or ""), "position": "P",
                         "points": round(points, 2)})
    return pd.DataFrame(rows, columns=[*_ID_COLUMNS, "points"])


# --- NFL, scored per simulation ---------------------------------------------
# The linear pass above cannot price a milestone bonus (+3 at 300 pass / 100
# rush / 100 rec yards): at the mean it overstates every player. On the
# correlated prop sim's sample arrays the bonus is a probability read off
# the samples, and the mean of the per-sim score is the bonus-inclusive
# projection (docs/SYSTEM_REVIEW.md §6.2–6.3). Interceptions and fumbles are
# not simulated and enter at their FantasyPros means.
NFL_BONUS = 3.0


def nfl_dk_points_from_samples(
    samples: Mapping[tuple[str, str], np.ndarray],
    player_key: str,
    *,
    interceptions: float = 0.0,
    fumbles: float = 0.0,
) -> np.ndarray | None:
    """Per-sim DK classic points for one player from the prop sim's arrays.

    ``samples`` is :func:`velocity.models.props_football.simulate_team_props`
    output — ``(player_key, market) → array``. ``None`` when the player has
    no simulated market at all.
    """
    markets = {m: samples.get((player_key, m)) for m in
               ("pass_yards", "pass_tds", "rush_yards", "receptions",
                "receiving_yards", "anytime_td")}
    present = [v for v in markets.values() if v is not None]
    if not present:
        return None
    n = len(present[0])
    zero = np.zeros(n)
    pass_yds = markets["pass_yards"] if markets["pass_yards"] is not None else zero
    rush_yds = markets["rush_yards"] if markets["rush_yards"] is not None else zero
    rec_yds = markets["receiving_yards"] if markets["receiving_yards"] is not None else zero
    total = (
        0.04 * pass_yds + NFL_BONUS * (pass_yds >= 300.0)
        + 4.0 * (markets["pass_tds"] if markets["pass_tds"] is not None else zero)
        + 0.1 * rush_yds + NFL_BONUS * (rush_yds >= 100.0)
        + 1.0 * (markets["receptions"] if markets["receptions"] is not None else zero)
        + 0.1 * rec_yds + NFL_BONUS * (rec_yds >= 100.0)
        + 6.0 * (markets["anytime_td"] if markets["anytime_td"] is not None else zero)
        - 1.0 * interceptions - 1.0 * fumbles
    )
    return np.asarray(total, dtype=float)


def nfl_sim_points(
    fp: pd.DataFrame,
    home_team: str,
    away_team: str,
    rng: np.random.Generator,
    config: object | None = None,
) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """Bonus-inclusive expected DK points AND per-sim arrays for one game.

    Returns the scorer-shaped frame (``player_id, player_name, team,
    position, points``) for every simulated player, and ``name → per-sim
    points`` for the GPP builder. Players the sim skips (below its volume
    floors) fall back to the linear scorer in the caller.
    """
    from velocity.models.props_football import simulate_team_props, team_player_means

    wide = _wide(fp)

    def _mean(row: Mapping[Any, Any], *keys: str) -> float:
        for key in keys:
            value = row.get(key)
            if value is None:
                continue
            number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
            if pd.notna(number):
                return float(number)
        return 0.0

    means_by_name = {
        str(r["player_name"]): (_mean(r, "pass_int", "pass_ints"),
                                _mean(r, "fumbles", "fumbles_lost"))
        for r in wide.to_dict("records")
    }
    rows: list[dict[str, object]] = []
    arrays: dict[str, np.ndarray] = {}
    for team in (home_team, away_team):
        players = team_player_means(fp, team)
        samples = simulate_team_props(players, rng, config)  # type: ignore[arg-type]
        for p in players:
            ints, fum = means_by_name.get(p.name, (0.0, 0.0))
            pts = nfl_dk_points_from_samples(samples, p.key, interceptions=ints, fumbles=fum)
            if pts is None:
                continue
            arrays[p.name] = pts
            rows.append({"player_id": p.key, "player_name": p.name, "team": team,
                         "position": p.position, "points": round(float(pts.mean()), 2)})
    frame = (pd.DataFrame(rows)[[*_ID_COLUMNS, "points"]] if rows
             else pd.DataFrame(columns=[*_ID_COLUMNS, "points"]))
    return frame, arrays


def dk_expected_points_wnba(player_box: pd.DataFrame) -> pd.DataFrame:
    """WNBA banked player boxes → expected DK points per game.

    The WNBA's counterpart to :func:`dk_expected_points_mlb`, and it takes the
    same kind of input: not a projection service's frame (none serves this
    league for free) but the league's own banked box scores, which
    ``scripts/build_wnba_player_box.py`` commits. The model behind it is a
    per-minute DK rate times expected minutes, both shrunk toward a positional
    prior (:mod:`velocity.models.dfs_wnba`).

    Returns the usual ``[player_id, player_name, team, position, points]``, so
    the pool join and the optimizer consume it unchanged.
    """
    from velocity.models.dfs_wnba import WnbaDfsModel

    if player_box.empty:
        return pd.DataFrame(columns=[*_ID_COLUMNS, "points"])
    return WnbaDfsModel.fit(player_box).projections(player_box)


def dk_expected_points_ncaaf(player_games: pd.DataFrame) -> pd.DataFrame:
    """College banked player-games → expected DK points per game.

    Like the WNBA scorer, its input is the league's own banked box scores
    rather than a projection service's frame — FantasyPros serves no college
    players, which is why the college board never built. The model is the
    football rate model on a six-game window (:mod:`velocity.models.dfs_ncaaf`).
    """
    from velocity.models.dfs_ncaaf import dk_expected_points_ncaaf as _project

    if player_games.empty:
        return pd.DataFrame(columns=[*_ID_COLUMNS, "points"])
    return _project(player_games)
