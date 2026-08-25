"""DK's salary-free formats — Tiers and Single Stat, where the model IS the edge.

Three of DK's formats hand you no salary cap at all:

* **Tiers** (MLB game type 45) — DK sorts the slate's players into six tiers
  and you take exactly one from each. Slot ids ascend with tier number
  (278…283 on the 8/25 board, T1 the stars down to the pitcher tier), and
  scoring is ordinary DK MLB scoring.
* **Single Stat - Home Runs** (MLB 346) — pick three hitters from the whole
  slate; your score is home runs.
* **Single Stat - Touchdowns** (CFB 364) — pick three players; your score is
  touchdowns scored.

With no cap there is no knapsack, which sounds easy and is exactly the
point: **the projection is the entire contest**. The only structure worth
respecting is DK's two shape rules, and each has an exact answer rather than
a repair heuristic:

* Tiers requires the roster to span at least two GAMES (``gameCount.minValue
  = 2``). If every tier's best pick sits in one game, the optimum moves
  exactly one tier — moving more only costs more, since each tier's best
  alternative elsewhere is by definition no better than its own best — so
  the fix is the cheapest single move.
* Single Stat requires at least two TEAMS (``teamCount.minValue = 2``). If
  the top three share a team, then every other player on the slate ranks
  below all three, so the optimum is the top two plus the best player from
  anywhere else. Again exact, not a nudge.

Pure functions of frames; the network layer stays in
``velocity.dfs.salaries``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

# DK's draft-stat attribute id for the number the lobby shows next to a
# player on these boards (FPPG on Tiers, season home runs on Single Stat).
_DK_STAT_ATTRIBUTE = 408


@dataclass(frozen=True)
class TierSpec:
    """One salary-free format: how many picks, and what shape they must take."""

    name: str
    n_picks: int
    one_per_tier: bool  # Tiers: exactly one player from each tier group
    stat: str  # what the contest actually scores
    min_games: int = 1
    min_teams: int = 1


MLB_TIERS = TierSpec("mlb_tiers", 6, True, "dk_points", min_games=2)
MLB_SINGLE_STAT_HR = TierSpec("mlb_single_stat_hr", 3, False, "home_runs",
                              min_teams=2)
CFB_SINGLE_STAT_TD = TierSpec("cfb_single_stat_td", 3, False, "touchdowns",
                              min_teams=2)

TIER_SPECS = {
    "Tiers": MLB_TIERS,
    "Single Stat - Home Runs": MLB_SINGLE_STAT_HR,
    "Single Stat - Touchdowns": CFB_SINGLE_STAT_TD,
}

_COLUMNS = ["draft_group_id", "player_id", "player_name", "position", "team",
            "competition", "kickoff", "status", "probable", "tier",
            "tier_slot_id", "dk_stat"]


def normalize_tiered(payload: dict[str, Any], draft_group_id: str) -> pd.DataFrame:
    """Flatten a salary-free DK board (Tiers / Single Stat) to one row per player.

    The salary normalizer cannot be reused: these boards ship ``salary:
    null`` on every draftable, and that normalizer drops a row without a
    price rather than guessing one. What replaces the price here is ``tier``
    — a 1-based ordinal derived from DK's roster-slot ids, which ascend with
    the tier number. A single-slot board (every Single Stat format) comes
    back as tier 1 throughout, which is the truth: there is one pool.
    """
    rows: list[dict[str, object]] = []
    for draftable in payload.get("draftables") or []:
        name = draftable.get("displayName")
        if name is None:
            continue
        competition = draftable.get("competition") or {}
        stat = next(
            (a.get("value") for a in draftable.get("draftStatAttributes") or []
             if a.get("id") == _DK_STAT_ATTRIBUTE), None)
        rows.append({
            "draft_group_id": str(draft_group_id),
            "player_id": str(draftable.get("playerDkId")
                             or draftable.get("playerId") or name),
            "player_name": str(name),
            "position": draftable.get("position"),
            "team": draftable.get("teamAbbreviation"),
            "competition": competition.get("name"),
            "kickoff": competition.get("startTime"),
            "status": draftable.get("status"),
            "probable": any(
                a.get("id") == 1 and str(a.get("value")).lower() == "true"
                for a in draftable.get("playerGameAttributes") or []),
            "tier_slot_id": draftable.get("rosterSlotId"),
            "dk_stat": stat,
        })
    if not rows:
        return pd.DataFrame(columns=_COLUMNS)
    df = pd.DataFrame(rows)
    kickoff = pd.to_datetime(df["kickoff"], errors="coerce", utc=True)
    df["kickoff"] = kickoff.dt.tz_localize(None)
    df["dk_stat"] = pd.to_numeric(df["dk_stat"], errors="coerce")
    # DK's slot ids ascend with the tier, so their sorted rank IS the tier.
    order = {slot: i + 1 for i, slot in
             enumerate(sorted(df["tier_slot_id"].dropna().unique()))}
    df["tier"] = df["tier_slot_id"].map(order).fillna(1).astype(int)
    df = df.drop_duplicates(subset=["player_id", "tier_slot_id"])
    return df[_COLUMNS].reset_index(drop=True)


@dataclass(frozen=True)
class TierPick:
    """One filled slot on a salary-free board."""

    slot: str
    player_name: str
    position: str
    team: str | None
    tier: int
    points: float
    kickoff: pd.Timestamp | None = None


@dataclass(frozen=True)
class TierEntry:
    """A legal Tiers / Single Stat entry and its projected total."""

    picks: tuple[TierPick, ...]
    total_points: float
    spec: TierSpec


def _row_game(row: dict[str, Any]) -> str:
    return str(row.get("competition") or row.get("kickoff") or "")


def _pick(row: dict[str, Any], slot: str, points: float) -> TierPick:
    kickoff = row.get("kickoff")
    return TierPick(
        slot=slot,
        player_name=str(row["player_name"]),
        position=str(row.get("position") or ""),
        team=None if pd.isna(row.get("team")) else str(row.get("team")),
        tier=int(row.get("tier") or 1),
        points=float(points),
        kickoff=None if kickoff is None or pd.isna(kickoff)
        else pd.Timestamp(kickoff),
    )


def _best_one_per_tier(pool: pd.DataFrame, spec: TierSpec) -> list[dict] | None:
    """Argmax per tier, then the cheapest single move if it spans one game."""
    tiers = sorted(pool["tier"].unique())
    if len(tiers) < spec.n_picks:
        return None
    chosen: list[dict] = []
    for tier in tiers[:spec.n_picks]:
        group = pool[pool["tier"] == tier]
        if group.empty:
            return None
        chosen.append(group.loc[group["points"].idxmax()].to_dict())
    if spec.min_games < 2 or len({_row_game(row) for row in chosen}) >= 2:
        return chosen
    # Every tier's best sits in one game. Move exactly one tier, at the
    # smallest loss — moving more can only cost more.
    game = _row_game(chosen[0])
    best_swap: tuple[float, int, dict] | None = None
    for i, row in enumerate(chosen):
        group = pool[(pool["tier"] == row["tier"])
                     & (pool.apply(lambda r: _row_game(r.to_dict()), axis=1) != game)]
        if group.empty:
            continue
        alternative = group.loc[group["points"].idxmax()].to_dict()
        loss = float(row["points"]) - float(alternative["points"])
        if best_swap is None or loss < best_swap[0]:
            best_swap = (loss, i, alternative)
    if best_swap is None:
        return None  # a one-game board cannot satisfy the rule
    _loss, index, alternative = best_swap
    chosen[index] = alternative
    return chosen


def _best_top_n(pool: pd.DataFrame, spec: TierSpec) -> list[dict] | None:
    """Top n by projection, with the exact fix when they share a team."""
    if len(pool) < spec.n_picks:
        return None
    ranked = pool.sort_values("points", ascending=False)
    chosen = ranked.head(spec.n_picks).to_dict("records")
    if spec.min_teams < 2:
        return chosen
    teams = {row.get("team") for row in chosen}
    if len(teams) >= 2:
        return chosen
    # All n share a team, so every other player ranks below all of them: the
    # optimum keeps the top n-1 and adds the best player from anywhere else.
    team = chosen[0].get("team")
    others = ranked[ranked["team"] != team]
    if others.empty:
        return None
    return chosen[:spec.n_picks - 1] + [others.iloc[0].to_dict()]


def build_tier_entry(
    pool: pd.DataFrame, *, spec: TierSpec = MLB_TIERS
) -> TierEntry | None:
    """The best legal entry for a salary-free board, or ``None`` if infeasible.

    ``pool`` needs ``player_name``/``points`` and, for a Tiers board,
    ``tier``. ``team`` and ``competition`` feed DK's two shape rules; where
    they are absent the corresponding rule simply does not apply.
    """
    for col in ("player_name", "points"):
        if col not in pool.columns:
            raise ValueError(f"pool needs a {col!r} column")
    pool = pool.dropna(subset=["points"]).copy()
    if "tier" not in pool.columns:
        pool["tier"] = 1
    if "team" not in pool.columns:
        pool["team"] = None
    pool["points"] = pool["points"].astype(float)
    pool = (
        pool.sort_values("points", ascending=False)
        .drop_duplicates(subset=["player_name"])
        .reset_index(drop=True)
    )
    chosen = (_best_one_per_tier(pool, spec) if spec.one_per_tier
              else _best_top_n(pool, spec))
    if chosen is None or len(chosen) < spec.n_picks:
        return None
    picks = [
        _pick(row, f"T{int(row['tier'])}" if spec.one_per_tier else "UTIL",
              float(row["points"]))
        for row in chosen
    ]
    return TierEntry(
        picks=tuple(picks),
        total_points=round(sum(p.points for p in picks), 2),
        spec=spec,
    )


def tier_frame(entry: TierEntry, draft_group_id: str = "") -> pd.DataFrame:
    """A built entry as a persistable frame (the site's DFS surface reads it)."""
    return pd.DataFrame([
        {"slot": p.slot, "player_name": p.player_name, "position": p.position,
         "team": p.team, "tier": p.tier, "points": p.points, "kickoff": p.kickoff}
        for p in entry.picks
    ]).assign(draft_group_id=str(draft_group_id), format=entry.spec.name)
