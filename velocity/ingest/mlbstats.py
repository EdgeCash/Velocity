"""MLB statsapi season stats → the DFS projections frame (free, keyless).

The FantasyPros public tier turned out not to serve MLB projections at all
(the 2026-08-23 dispatches answered ``public_api_limited`` and zero players
for ALL and for every per-position fallback), so the MLB DFS pool prices
from the league's own statsapi instead: season-to-date hitting and pitching
totals for every player, melted into the same long ``(player, stat, value)``
shape the FantasyPros normalizer emits — so
:func:`velocity.dfs.scoring.dk_expected_points_mlb` consumes it unchanged
(hitters ÷ games, pitchers ÷ starts). Season-to-date rates are arguably the
better projection anyway: they refresh with every slate run and carry no
external dependency beyond the statsapi the pipeline already trusts for
probables and box scores.

Two layers, kept strictly separate so the test gate stays offline (the
repo-wide ingest pattern):

* :func:`normalize_season_stats` — **pure**: one stats payload → long rows.
* :func:`fetch_season_stats` — the network layer (paginated).
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Mapping
from typing import Any

import pandas as pd

_STATS_URL = (
    "https://statsapi.mlb.com/api/v1/stats?stats=season&group={group}"
    "&season={season}&sportId=1&playerPool=all&limit={limit}&offset={offset}"
)
_FETCH_TIMEOUT = 60
_USER_AGENT = "velocity/1.0"
_PAGE = 1000

# statsapi stat key → the scorer's lowercase alias vocabulary
# (velocity.dfs.scoring._MLB_*_WEIGHTS). Hitting and pitching share spellings
# like "hits"/"baseOnBalls" with opposite meanings, so each group maps only
# its own keys and a player contributes rows from one group (see
# ``stats_long_frame``).
HITTING_KEYS = {
    "gamesPlayed": "g",
    "hits": "h",
    "doubles": "2b",
    "triples": "3b",
    "homeRuns": "hr",
    "rbi": "rbi",
    "runs": "r",
    "baseOnBalls": "bb",
    "hitByPitch": "hbp",
    "stolenBases": "sb",
}
PITCHING_KEYS = {
    "gamesPlayed": "g",
    "gamesStarted": "gs",
    "inningsPitched": "ip",
    "strikeOuts": "k",
    "wins": "w",
    "earnedRuns": "er",
    "hits": "h",
    "baseOnBalls": "bb",
}

_COLUMNS = ["season", "week", "player_id", "player_name", "team", "position",
            "stat", "value", "source"]


def _innings(value: Any) -> float | None:
    """statsapi ``inningsPitched`` ("123.2" = 123⅔) → true decimal innings."""
    try:
        text = str(value)
        whole, _, frac = text.partition(".")
        outs = int(frac or 0)
        return float(int(whole)) + outs / 3.0
    except (TypeError, ValueError):
        return None


def _number(stat: str, value: Any) -> float | None:
    if stat == "ip":
        return _innings(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_season_stats(
    payload: Mapping[str, Any], group: str, season: int
) -> pd.DataFrame:
    """One statsapi season-stats payload → the long projections frame.

    ``group`` is ``hitting`` or ``pitching`` and selects the stat-key map.
    Rows are tagged ``week=0`` (season totals) and ``source="statsapi"`` —
    the exact shape the FantasyPros normalizer emits, so downstream scoring
    is shared.
    """
    keys = HITTING_KEYS if group == "hitting" else PITCHING_KEYS
    rows: list[dict[str, object]] = []
    stats = payload.get("stats") or []
    splits = stats[0].get("splits", []) if stats else []
    for split in splits:
        player = split.get("player") or {}
        name = player.get("fullName")
        if not name:
            continue
        # Position sits on the SPLIT in season-stats payloads; the player
        # object carries primaryPosition only on some shapes — try both.
        position = ((split.get("position") or {}).get("abbreviation")
                    or (player.get("primaryPosition") or {}).get("abbreviation"))
        team = (split.get("team") or {}).get("name")
        stat_block = split.get("stat") or {}
        for raw_key, stat in keys.items():
            value = _number(stat, stat_block.get(raw_key))
            if value is None:
                continue
            rows.append({
                "season": season,
                "week": 0,
                "player_id": None if player.get("id") is None else str(player["id"]),
                "player_name": str(name),
                "team": None if team is None else str(team),
                "position": None if position is None else str(position),
                "stat": stat,
                "value": value,
                "source": "statsapi",
            })
    return pd.DataFrame(rows, columns=_COLUMNS)


def _get(url: str) -> Any:  # pragma: no cover - network
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
        return json.loads(resp.read())


def fetch_season_stats(season: int, group: str) -> pd.DataFrame:  # pragma: no cover - network
    """Every player's season totals for ``group``, across statsapi pages."""
    frames: list[pd.DataFrame] = []
    offset = 0
    while True:
        payload = _get(_STATS_URL.format(group=group, season=season,
                                         limit=_PAGE, offset=offset))
        frame = normalize_season_stats(payload, group, season)
        frames.append(frame)
        stats = payload.get("stats") or [{}]
        total = int(stats[0].get("totalSplits") or 0)
        offset += _PAGE
        if offset >= total or frame.empty:
            break
        time.sleep(0.2)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=_COLUMNS)


def stats_long_frame(
    hitting: pd.DataFrame, pitching: pd.DataFrame
) -> pd.DataFrame:
    """Combine the two groups, one group per player.

    Pitchers who batted appear in both groups with colliding stat spellings
    (a pitcher's "h" is hits ALLOWED; a batter's is hits), so each player
    keeps only their primary group: position ``P`` → pitching rows, everyone
    else (two-way players included — the bat is the everyday role) → hitting.
    """
    hit = hitting[hitting["position"].astype(str) != "P"] if not hitting.empty \
        else hitting
    pit = pitching[pitching["position"].astype(str) == "P"] if not pitching.empty \
        else pitching
    out = pd.concat([hit, pit], ignore_index=True)
    return out.reset_index(drop=True)
