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
