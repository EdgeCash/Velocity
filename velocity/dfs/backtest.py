"""Shared machinery for DFS lineup backtests — boards in, realized points out.

A DFS backtest needs two halves that live in different places: DK's historical
**boards** (salaries, pools, captain prices — harvested by walking retired
draft-group ids) and the **box scores** (what each player actually banked).
This module owns the joins and the scoring that every format's backtest needs
identically, so the per-format scripts hold only the roster rules.

Three things here are load-bearing, and two of them are lessons:

* :func:`norm` folds accents before stripping punctuation. DK spells
  "Sanchez", statsapi spells "Sánchez"; deleting the accented letter instead
  of folding it silently drops the player.
* :func:`player_day_index` keys on (name, **date**), not name alone. A player
  appears in at most one game a day, so the pair is unique — and using the
  date is what keeps a three-game series apart. Matching a board to a box
  score by roster overlap alone lands on an arbitrary game of the series,
  because the same twenty-six names play every night.
* a rostered player with no box-score row scores **0.0**, which is exactly
  what DK pays a player who never appears. No leakage, no free pass.

Pure functions of frames; offline-testable, no network.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from velocity.models.dfs_mlb import hitter_dk_points, pitcher_dk_points


def norm(name: object) -> str:
    """Name key that survives DK vs statsapi spelling ("Sánchez"/"Sanchez")."""
    folded = unicodedata.normalize("NFKD", str(name)).encode(
        "ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


def prepare_banks(
    batters: pd.DataFrame, starters: pd.DataFrame, games: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Bank frames joined to game context and scored the way DK scores.

    Returns ``(batters, starters, played)``: the two player frames carrying
    ``actual`` DK points and a normalized name ``key``, and the games that
    finished (an unplayed game has nothing to score against).
    """
    games = games.copy()
    games["game_id"] = games["game_id"].astype(str)
    games["kickoff"] = pd.to_datetime(games["kickoff"], errors="coerce")
    played = games.dropna(subset=["kickoff"])
    played = played[played["home_score"].notna()].sort_values("kickoff")

    context = ["game_id", "kickoff", "home_team", "away_team"]
    bat = batters.copy()
    bat["game_id"] = bat["game_id"].astype(str)
    bat["batter_id"] = bat["batter_id"].astype(str)
    bat = bat.merge(played[[*context, "season"]], on="game_id", how="inner")
    bat["actual"] = hitter_dk_points(bat)
    bat["key"] = bat["batter_name"].map(norm)

    sp = starters.copy()
    sp["game_id"] = sp["game_id"].astype(str)
    sp["starter_id"] = sp["starter_id"].astype(str)
    sp = sp.merge(played[context], on="game_id", how="inner")
    sp["actual"] = pitcher_dk_points(sp)
    sp["key"] = sp["starter_name"].map(norm)
    return bat, sp, played


def player_day_index(
    bat: pd.DataFrame, sp: pd.DataFrame
) -> dict[tuple[str, object], dict[str, Any]]:
    """(normalized name, date) → what that player did that day.

    The key that makes a multi-game board joinable: a player appears in at
    most one game per day, so the pair identifies him without needing to
    match the board to a game first. Each record carries the bank id, the
    realized DK points, the game and side, the lineup slot, and the team.
    """
    index: dict[tuple[str, object], dict[str, Any]] = {}
    for frame, id_column, is_pitcher in ((bat, "batter_id", False),
                                         (sp, "starter_id", True)):
        if frame.empty:
            continue
        for row in frame.to_dict("records"):
            day = pd.Timestamp(row["kickoff"]).normalize().date()
            index[(str(row["key"]), day)] = {
                "player_id": str(row[id_column]),
                "actual": float(row["actual"]),
                "game_id": str(row["game_id"]),
                "side": str(row.get("side") or ""),
                "home_team": str(row.get("home_team") or ""),
                "lineup_slot": (int(row["lineup_slot"])
                                if not is_pitcher and row.get("lineup_slot")
                                else None),
                # Was he in the posted card? The announced nine are exactly
                # the batters a build made after lineups drop can choose
                # from, so this reconstructs that pool without guessing.
                "started": bool(row.get("started")) if not is_pitcher else True,
                "is_pitcher": is_pitcher,
            }
    return index


def opposing_starters(sp: pd.DataFrame) -> dict[tuple[str, str], str]:
    """(game_id, side) → the starter that side FACES, for the hitter model."""
    if sp.empty:
        return {}
    flip = {"home": "away", "away": "home"}
    return {
        (str(row["game_id"]), flip.get(str(row["side"]), "")): str(row["starter_id"])
        for row in sp.to_dict("records")
    }


def realized(
    slots: Sequence[Any], actual_of: Mapping[str, float], *,
    captain_slot: str = "CPT", multiplier: float = 1.5,
) -> float:
    """A built roster's realized DK points, captain multiplier applied."""
    total = 0.0
    for slot in slots:
        factor = multiplier if slot.slot == captain_slot else 1.0
        total += factor * float(actual_of.get(slot.player_name, 0.0))
    return total


def random_rosters(
    pool: pd.DataFrame, rng: np.random.Generator, *, size: int, n: int,
    cap: int = 50_000, min_teams: int = 1, captain: bool = False,
    groups: Sequence[tuple[str, int]] | None = None,
    max_attempts_per: int = 60,
) -> np.ndarray:
    """Realized scores of ``n`` random LEGAL rosters — the field proxy.

    Not a model: the floor a contest entrant clears by filling the slots
    under the cap. ``groups`` names the position quota (``[("P", 2), ("C",
    1), ...]``) so a classic roster is sampled legally rather than as ten
    arbitrary players; without it the pool is treated as one bucket, which is
    right for showdown's flex-only shape. Rejection sampling, bounded so a
    pool with no legal roster returns empty rather than spinning.
    """
    salary = pool["salary"].to_numpy()
    captain_salary = (pool["captain_salary"].to_numpy() if captain
                      else pool["salary"].to_numpy())
    actual = pool["actual"].to_numpy()
    teams = pool["team"].astype(str).to_numpy()
    if groups is not None:
        positions = pool["position"].astype(str).to_numpy()
        buckets = [(np.flatnonzero(positions == position), k)
                   for position, k in groups]
        if any(len(rows) < k for rows, k in buckets):
            return np.array([])
    scores: list[float] = []
    attempts = 0
    while len(scores) < n and attempts < n * max_attempts_per:
        attempts += 1
        if groups is None:
            picks = rng.choice(len(pool), size=size, replace=False)
        else:
            picks = np.concatenate([rng.choice(rows, size=k, replace=False)
                                    for rows, k in buckets])
        head, tail = picks[0], picks[1:]
        cost = (captain_salary[head] + salary[tail].sum() if captain
                else salary[picks].sum())
        if cost > cap:
            continue
        if min_teams > 1 and len(set(teams[picks])) < min_teams:
            continue
        scores.append(1.5 * actual[head] + actual[tail].sum() if captain
                      else float(actual[picks].sum()))
    return np.array(scores)


def recent_slots(bat: pd.DataFrame, before: pd.Timestamp) -> dict[str, int]:
    """Each batter's most recent STARTING lineup slot before a timestamp.

    The honest pregame guess, and the one the live pipeline makes. Reading
    the slot out of the game being scored would be leakage twice over: it
    states where he hit *and* that he played at all.
    """
    if bat.empty:
        return {}
    past = bat[bat["kickoff"] < before]
    if "started" in past.columns:
        past = past[past["started"].astype(bool)]
    if past.empty:
        return {}
    latest = past.sort_values("kickoff").drop_duplicates("batter_id", keep="last")
    return {str(row["batter_id"]): int(row["lineup_slot"])
            for row in latest.to_dict("records")
            if row.get("lineup_slot")}


def board_games(
    board: pd.DataFrame, index: Mapping[tuple[str, object], dict[str, Any]]
) -> dict[str, str]:
    """DK ``competition`` string → the bank ``game_id`` it names.

    Resolved by which game the board's players turned up in that day — DK's
    team abbreviations and statsapi's full club names share no vocabulary.
    This is a *schedule* lookup, not a result: which game a board covers is
    public before lock, so reconstructing it from the box score's identity
    (never its contents) leaks nothing.
    """
    out: dict[str, str] = {}
    for competition, rows in board.groupby(board["competition"].astype(str)):
        votes: dict[str, int] = {}
        for row in rows.to_dict("records"):
            stamp = pd.Timestamp(row["kickoff"])
            if pd.isna(stamp):
                continue
            found = index.get((norm(row["player_name"]), stamp.normalize().date()))
            if found is not None:
                votes[found["game_id"]] = votes.get(found["game_id"], 0) + 1
        if votes:
            out[str(competition)] = max(votes, key=lambda g: votes[g])
    return out


def board_probables(board: pd.DataFrame) -> dict[str, list[str]]:
    """DK ``competition`` → the announced starting pitchers on that board.

    Straight off DK's own probable flag, which is pregame information: the
    board states who is starting before anyone locks a roster.
    """
    if "probable" not in board.columns:
        return {}
    flagged = board[board["probable"].fillna(False).astype(bool)]
    return {
        str(competition): list(rows["player_name"].astype(str))
        for competition, rows in flagged.groupby(flagged["competition"].astype(str))
    }
