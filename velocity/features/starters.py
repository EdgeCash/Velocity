"""Who is actually starting at quarterback — the projection-time starter map.

The QB-adjusted fit (:func:`velocity.features.team.fit_qb_ratings`) prices
each team with the passer it saw last: the primary passer in the team's latest
training game. Mid-season that is nearly always right. It is wrong in exactly
the moments that move a line most:

* **Week 1.** A team that clinched and rested in Week 18 played its backup, so
  the fit believes the backup is the starter. Measured on the 2026 Week-1
  board: 11 of 32 teams mismatched their 2025 leading passer, and Kansas City
  projected 5.6 points a game low because the fit had Chris Oladokun at
  quarterback (docs/SYSTEM_REVIEW.md §3.1).
* **Offseason moves and in-season injuries.** No rule that reads only last
  season's plays can see either; the ratings keep pricing the old starter
  until the new one has thrown forty dropbacks.

This module supplies the starter from a source that *does* see the present:
the FantasyPros projections snapshot names every team's quarterbacks with a
projected workload (even the season-long snapshot does — it lists the 2026
depth chart), and the injuries snapshot says who is out. The map it returns is
keyed the way :class:`~velocity.features.team.QBTeamRatings.starters` is —
team → nflverse player id — so the runner applies it with one
:func:`dataclasses.replace` and every wrapper model, every prop, and every DFS
projection on that team follows.

Pure functions of frames; a name that resolves to nothing leaves the fit's own
detection in place and is reported, never guessed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import pandas as pd

# FantasyPros club codes that differ from the nflverse keys the ratings use.
FP_CODE_FIXUPS: Mapping[str, str] = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS"}

# The workload stat that orders a team's quarterbacks (QB1 first).
DEFAULT_WORKLOAD_STAT = "pass_yds"

_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def normalize_player_name(name: object) -> str:
    """Lowercase alphanumerics with generational suffixes dropped.

    "Michael Penix Jr." and "Michael Penix" are the same person to a
    projections feed and a stats feed that disagree about the suffix.
    """
    lowered = re.sub(r"[.\-']", "", str(name).lower())
    lowered = _SUFFIXES.sub(" ", lowered)
    return re.sub(r"[^a-z0-9]+", "", lowered)


def qb_depth_by_team(
    fp: pd.DataFrame,
    *,
    stat: str = DEFAULT_WORKLOAD_STAT,
    team_fixups: Mapping[str, str] = FP_CODE_FIXUPS,
) -> dict[str, list[str]]:
    """Team code → quarterback names ordered by projected workload, QB1 first.

    Reads the FantasyPros long frame (``player_name/team/position/stat/value``).
    A quarterback with no workload row is ordered last; a team with no
    quarterbacks at all is absent.
    """
    if fp.empty:
        return {}
    qbs = fp[fp["position"].astype(str).str.upper() == "QB"]
    if qbs.empty:
        return {}
    workload = qbs[qbs["stat"].astype(str) == stat]
    by_player = (
        workload.groupby(["team", "player_name"])["value"]
        .apply(lambda s: pd.to_numeric(s, errors="coerce").max())
    )
    everyone = qbs[["team", "player_name"]].drop_duplicates()
    out: dict[str, list[tuple[float, str]]] = {}
    for team, name in everyone.itertuples(index=False):
        code = team_fixups.get(str(team), str(team))
        load = float(by_player.get((team, name), float("nan")))
        out.setdefault(code, []).append((-1.0 if pd.isna(load) else load, str(name)))
    return {
        team: [name for _load, name in sorted(names, key=lambda pair: (-pair[0], pair[1]))]
        for team, names in out.items()
    }


def nflverse_ids(player_weeks: pd.DataFrame, *, position: str = "QB") -> dict[str, str]:
    """Normalized full name → nflverse player id, the newest season winning a tie.

    Reads the committed player-weeks bank (``player_id/player_name/position/
    season``). Restricting to one position keeps a shared name from crossing
    positions; among quarterbacks the bank carries no duplicates today.
    """
    if player_weeks.empty:
        return {}
    frame = player_weeks
    if "position" in frame.columns:
        frame = frame[frame["position"].astype(str).str.upper() == position]
    order = frame.sort_values("season") if "season" in frame.columns else frame
    out: dict[str, str] = {}
    for pid, name in zip(order["player_id"], order["player_name"], strict=False):
        out[normalize_player_name(name)] = str(pid)  # later seasons overwrite
    return out


def outs_by_team(
    injuries: pd.DataFrame | None, *, team_fixups: Mapping[str, str] = FP_CODE_FIXUPS
) -> dict[str, set[str]]:
    """Team code → normalized names of players the injuries snapshot marks out."""
    if injuries is None or injuries.empty or "is_out" not in injuries.columns:
        return {}
    out: dict[str, set[str]] = {}
    marked = injuries[injuries["is_out"].astype(bool)]
    for team, name in zip(marked["team"], marked["player_name"], strict=False):
        code = team_fixups.get(str(team), str(team))
        out.setdefault(code, set()).add(normalize_player_name(name))
    return out


def starter_map(
    fp: pd.DataFrame,
    player_weeks: pd.DataFrame,
    injuries: pd.DataFrame | None = None,
    *,
    stat: str = DEFAULT_WORKLOAD_STAT,
    team_fixups: Mapping[str, str] = FP_CODE_FIXUPS,
) -> tuple[dict[str, str], list[str]]:
    """Team → the nflverse id of the quarterback to price, plus log notes.

    For each team, the projected QB1 who is not on the injury report as out;
    an out QB1 demotes to the next projected passer. A name the stats bank
    cannot resolve to an id contributes nothing for that team (the fit's own
    detection stands) and a note says so. Returns ``(overrides, notes)``.
    """
    depth = qb_depth_by_team(fp, stat=stat, team_fixups=team_fixups)
    ids = nflverse_ids(player_weeks)
    outs = outs_by_team(injuries, team_fixups=team_fixups)
    overrides: dict[str, str] = {}
    notes: list[str] = []
    for team, names in sorted(depth.items()):
        team_outs = outs.get(team, set())
        for name in names:
            key = normalize_player_name(name)
            if key in team_outs:
                notes.append(f"{team}: {name} is out — next passer")
                continue
            pid = ids.get(key)
            if pid is None:
                notes.append(f"{team}: {name} has no nflverse id — keeping the fit's starter")
                break
            overrides[team] = pid
            break
    return overrides, notes


def describe_changes(
    overrides: Mapping[str, str],
    detected: Mapping[str, str],
    player_weeks: pd.DataFrame,
) -> list[str]:
    """Human-readable lines for every team whose starter the map changed."""
    names: dict[str, str] = {}
    if not player_weeks.empty:
        for pid, name in zip(player_weeks["player_id"], player_weeks["player_name"], strict=False):
            names[str(pid)] = str(name)
    lines = []
    for team, pid in sorted(overrides.items()):
        was = detected.get(team)
        if was == pid:
            continue
        lines.append(
            f"{team}: {names.get(pid, pid)}"
            + (f" (fit had {names.get(was, was)})" if was else " (fit had nobody)")
        )
    return lines
