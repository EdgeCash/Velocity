"""College DFS projections — the NFL rate model on a college player bank.

NCAAF was the only in-season sport with a DraftKings roster spec, daily salary
collection, and nothing at all to price a board with. The missing half was
data, not modelling: `velocity/ingest/cfb_players.py` supplies it, and the
model on top is deliberately the football one the repo already fitted and
tested (:class:`velocity.models.dfs_nfl.DfsNflModel`) — per-player DK points
per game, shrunk toward **his own position's** mean, which is the same
empirical-Bayes shape and the same reason (a quarterback and a receiver are
not two draws from one distribution).

What is college-specific is the **window**. A professional's career is one
long sample; a college player's is a roster that turns over every August, and
his usage moves with a depth chart rather than a contract. So the fit reads a
bounded number of his most recent games rather than everything banked —
swept walk-forward on the 2024 season below.

Pure functions of frames; offline-testable, no network.
"""

from __future__ import annotations

import pandas as pd

from velocity.ingest.cfb_players import CFB_POSITIONS

# The most recent games of a player's own history the fit reads. Swept
# walk-forward over the whole 2024 season — 29,217 player-games — and the
# curve has a real interior optimum rather than running to an endpoint: two
# games is too noisy (within-slate rank 0.4322), forty is too stale (0.4356),
# and six is the peak (0.4561). Half a college season, which is about what a
# roster that turns over every August should be worth.
RECENT_GAMES = 6


def recent_games(player_games: pd.DataFrame, n: int = RECENT_GAMES) -> pd.DataFrame:
    """Each player's ``n`` most recent games, newest last.

    Ordered by season then week, which is the only clock the bank carries.
    """
    if player_games.empty:
        return player_games
    ordered = player_games.sort_values(["season", "week"])
    return ordered.groupby("player_id", sort=False).tail(n)


def dk_expected_points_ncaaf(
    player_games: pd.DataFrame, *, before: tuple[int, int] | None = None,
    window: int = RECENT_GAMES,
) -> pd.DataFrame:
    """Banked college player-games → expected DK points per game.

    Returns ``[player_id, player_name, team, position, points]``, the shape
    every other scorer emits, so the pool join and the optimizer consume it
    unchanged.

    ``before`` is ``(season, week)`` and is what keeps a backtest honest: a
    projection may only read games played before the one it is projecting.
    Live callers leave it off — the bank only holds games already played.
    """
    from velocity.models.dfs_nfl import DfsNflModel

    columns = ["player_id", "player_name", "team", "position", "points"]
    if player_games.empty:
        return pd.DataFrame(columns=columns)
    frame = player_games.copy()
    frame["player_id"] = frame["player_id"].astype(str)
    if before is not None:
        season, week = before
        frame = frame[(frame["season"] < season)
                      | ((frame["season"] == season) & (frame["week"] < week))]
    frame = frame[frame["position"].astype(str).isin(CFB_POSITIONS)]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    fitted = recent_games(frame, window)
    model = DfsNflModel.fit(fitted, positions=CFB_POSITIONS)
    if not model.player_rate:
        return pd.DataFrame(columns=columns)

    # Identity comes from each player's most recent game, so a transfer
    # carries the club he plays for now rather than the one he left.
    latest = fitted.sort_values(["season", "week"]).drop_duplicates(
        "player_id", keep="last")
    rows = []
    for row in latest.to_dict("records"):
        points = model.project(str(row["player_id"]))
        if points is None:
            continue
        rows.append({
            "player_id": str(row["player_id"]),
            "player_name": str(row["player_name"]),
            "team": str(row.get("team") or ""),
            "position": str(row.get("position") or ""),
            "points": round(float(points), 2),
        })
    return pd.DataFrame(rows, columns=columns)
