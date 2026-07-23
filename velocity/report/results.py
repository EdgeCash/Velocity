"""Resolve final scores onto a persisted slate's game ids, for grading.

The slate the runner archives is keyed by **The Odds API** event id; final scores
come from **StatsAPI**, keyed by its own ``gamePk``. The two id spaces don't line
up, so grading needs a bridge: match each slate game to a StatsAPI final by the
resolved home/away **team codes** and the **game date** (both feeds carry team
names and a kickoff). That's the same tolerant team resolution the slate itself
uses, so a game that resolves for pricing resolves for grading.

Pure and offline-testable: :func:`finals_for_slate` takes the persisted games map
(``game_id`` + team names + kickoff) and a StatsAPI ``Games`` frame and returns a
finals frame keyed by the slate's ``game_id`` — exactly what
:func:`velocity.report.scorecard.grade_slate` wants. Unmatched or unplayed games
are dropped (they simply stay ungraded).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from velocity.wagering.live import MLB_TEAM_ALIASES, resolve_team


def _pair_key(away: str, home: str, when: Any, codes: list[str],
              aliases: Mapping[str, str]) -> tuple[str, str, object] | None:
    """``(away_code, home_code, date)`` key, or ``None`` if a team doesn't resolve."""
    a = resolve_team(str(away), codes, dict(aliases))
    h = resolve_team(str(home), codes, dict(aliases))
    if a is None or h is None:
        return None
    date = pd.Timestamp(when).date() if pd.notna(when) else None
    return (a, h, date)


def finals_for_slate(
    games_map: pd.DataFrame,
    schedule: pd.DataFrame,
    *,
    aliases: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Map StatsAPI finals onto the slate's ``game_id`` by team code + date.

    ``games_map`` has ``game_id``, ``home_team``, ``away_team``, ``kickoff`` (the
    slate side); ``schedule`` is a ``Games``-shaped frame (StatsAPI side) with team
    names, ``kickoff``, and ``home_score``/``away_score``. Returns
    ``[game_id, home_score, away_score]`` for every slate game matched to a played
    StatsAPI game.
    """
    alias_map = dict(MLB_TEAM_ALIASES if aliases is None else aliases)
    codes = list(alias_map.values())

    finals: dict[tuple[str, str, object], tuple[float, float]] = {}
    for row in schedule.to_dict("records"):
        key = _pair_key(row["away_team"], row["home_team"], row.get("kickoff"), codes, alias_map)
        hs, as_ = row.get("home_score"), row.get("away_score")
        if key is not None and pd.notna(hs) and pd.notna(as_):
            finals[key] = (float(hs), float(as_))

    rows: list[dict[str, object]] = []
    for game in games_map.to_dict("records"):
        key = _pair_key(
            game["away_team"], game["home_team"], game.get("kickoff"), codes, alias_map
        )
        if key is not None and key in finals:
            hs, as_ = finals[key]
            rows.append({"game_id": str(game["game_id"]), "home_score": hs, "away_score": as_})
    return pd.DataFrame(rows, columns=["game_id", "home_score", "away_score"])
