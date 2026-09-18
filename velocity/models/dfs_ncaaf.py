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


# Games of prior for a single stat's per-game rate — the NFL model's
# player prior (velocity.models.dfs_nfl.PLAYER_PRIOR_GAMES), so a two-game
# sample shrinks toward his position's rate the same way his DK points do.
STAT_PRIOR_GAMES = 4.0

# The bank's columns behind DK's Single Stat formats. Passing touchdowns
# are thrown, not scored, so they are deliberately absent from the
# touchdown stat — DK pays the player who reaches the end zone.
TOUCHDOWNS_SCORED = ("rush_tds", "receiving_tds")
TOTAL_YARDS = ("pass_yards", "rush_yards", "receiving_yards")


def expected_stat_ncaaf(
    player_games: pd.DataFrame, stats: tuple[str, ...] = TOUCHDOWNS_SCORED, *,
    window: int = RECENT_GAMES, prior_games: float = STAT_PRIOR_GAMES,
) -> pd.DataFrame:
    """Banked college player-games → an expected per-game stat, shrunk.

    The Single Stat boards score one number (touchdowns scored, total yards)
    rather than DK points, and the FantasyPros-shaped scorer the NFL boards
    use has nothing to read on the college bank. This is the same shape as
    :func:`dk_expected_points_ncaaf` — ``[player_id, player_name, team,
    position, points]`` with ``points`` the expected stat — over the same
    six-game window, with each player's rate shrunk toward his position's
    mean on ``prior_games`` of prior (empirical Bayes, as the DK-points model
    does). Identity comes from the most recent game, so a transfer carries
    the club he plays for now.
    """
    columns = ["player_id", "player_name", "team", "position", "points"]
    if player_games.empty:
        return pd.DataFrame(columns=columns)
    frame = player_games.copy()
    frame["player_id"] = frame["player_id"].astype(str)
    frame = frame[frame["position"].astype(str).isin(CFB_POSITIONS)]
    present = [c for c in stats if c in frame.columns]
    if frame.empty or not present:
        return pd.DataFrame(columns=columns)
    fitted = recent_games(frame, window).copy()
    fitted["_stat"] = sum(
        pd.to_numeric(fitted[c], errors="coerce").fillna(0.0) for c in present)
    position_mean = fitted.groupby("position")["_stat"].mean()
    by_player = fitted.groupby("player_id").agg(
        total=("_stat", "sum"), games=("_stat", "size"))
    latest = fitted.sort_values(["season", "week"]).drop_duplicates(
        "player_id", keep="last").set_index("player_id")
    rows = []
    for pid, row in by_player.iterrows():
        who = latest.loc[str(pid)]
        prior = float(position_mean.get(str(who["position"]), 0.0))
        rate = (float(row["total"]) + prior * prior_games) / (float(row["games"]) + prior_games)
        rows.append({
            "player_id": str(pid),
            "player_name": str(who["player_name"]),
            "team": str(who.get("team") or ""),
            "position": str(who.get("position") or ""),
            "points": round(rate, 4),
        })
    return pd.DataFrame(rows, columns=columns)
