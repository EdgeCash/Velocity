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
