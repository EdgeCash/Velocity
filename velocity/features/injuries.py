"""The injury burden — how much of a team's production is ruled out this week.

The projection prices the quarterback's absence (the starter map, the
schedule's announced starters in the backtest) and nothing else. The banked
designations (``datasets/nfl/injuries.parquet``, 2011–2026) and the usage
bank (``datasets/nfl/player_weeks.parquet``, 2020–2026) together say who
else is out and how much of the offense ran through them: on the committed
frames a team-week has 2.6 players Out or Doubtful, carrying on average 4%
of its prior-season touches, and one team-week in ten loses 15% or more.

The feature is one number per team-week, ``burden`` — the sum over players
ruled Out or Doubtful of their share of the team's targets and carries — and
the model takes it off the team's expected points at ``points_per_unit`` per
whole team's worth of production. Quarterbacks are excluded, because the
starter machinery already prices them and a passer's share of targets and
carries is not what his absence costs.

Shares entering a week are the season to date once four weeks have been
played, and the previous season's full-season shares before that; a player
with no history on the team (a rookie, a signing) has no share and costs
nothing, which understates the burden and is the honest direction.
"""

from __future__ import annotations

from collections.abc import Collection

import pandas as pd

# Weeks of the current season before the season-to-date shares take over
# from the previous season's.
SEASON_TO_DATE_FROM_WEEK = 5
# Positions whose absence the starter machinery already prices.
EXCLUDED_POSITIONS: frozenset[str] = frozenset({"QB"})


def _opportunities(player_weeks: pd.DataFrame) -> pd.DataFrame:
    out = player_weeks[["season", "week", "team", "player_id", "position"]].copy()
    zero = pd.Series(0.0, index=player_weeks.index)
    targets = (pd.to_numeric(player_weeks["targets"], errors="coerce").fillna(0.0)
               if "targets" in player_weeks.columns else zero)
    carries = (pd.to_numeric(player_weeks["carries"], errors="coerce").fillna(0.0)
               if "carries" in player_weeks.columns else zero)
    out["opp"] = (targets + carries).to_numpy(dtype=float)
    out["season"] = out["season"].astype(int)
    out["week"] = out["week"].astype(int)
    out["team"] = out["team"].astype(str)
    out["player_id"] = out["player_id"].astype(str)
    return out


def _shares(opps: pd.DataFrame) -> pd.DataFrame:
    """Each player's share of his team's opportunities in ``opps``."""
    if opps.empty:
        return pd.DataFrame(columns=["team", "player_id", "share"])
    by_player = opps.groupby(["team", "player_id"], as_index=False)["opp"].sum()
    team_total = by_player.groupby("team")["opp"].transform("sum")
    by_player["share"] = (by_player["opp"] / team_total.where(team_total > 0)).fillna(0.0)
    return pd.DataFrame(by_player[["team", "player_id", "share"]])


def shares_entering(player_weeks: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    """Usage shares a projection may use entering ``(season, week)``.

    Season to date (weeks strictly before ``week``) from
    :data:`SEASON_TO_DATE_FROM_WEEK` on; the previous season's full-season
    shares before that. ``team``/``player_id``/``share`` per row.
    """
    opps = _opportunities(player_weeks)
    if week >= SEASON_TO_DATE_FROM_WEEK:
        window = opps[(opps["season"] == season) & (opps["week"] < week)]
    else:
        window = opps[opps["season"] == season - 1]
    return _shares(window)


def burden_by_team_week(
    injuries: pd.DataFrame,
    player_weeks: pd.DataFrame,
    *,
    exclude_positions: Collection[str] = EXCLUDED_POSITIONS,
) -> pd.DataFrame:
    """``season``/``week``/``team``/``burden`` for every team-week with a designation.

    ``burden`` is the sum of the usage shares of the players ruled Out or
    Doubtful (``is_out``) that week, quarterbacks excluded. Team-weeks with
    no usage history (before the usage bank starts) get no row: absence of a
    number is never a zero burden.
    """
    outs = injuries[injuries["is_out"].astype(bool)].copy()
    outs = outs.dropna(subset=["season", "week", "team", "player_id"])
    if exclude_positions:
        outs = outs[~outs["position"].astype(str).str.upper().isin(
            {p.upper() for p in exclude_positions})]
    outs["season"] = outs["season"].astype(int)
    outs["week"] = outs["week"].astype(int)
    outs["team"] = outs["team"].astype(str)
    outs["player_id"] = outs["player_id"].astype(str)
    rows: list[dict[str, object]] = []
    usage_seasons = pd.to_numeric(player_weeks["season"], errors="coerce").dropna()
    seasons_with_usage = set(usage_seasons.astype(int))
    for key, group in outs.groupby(["season", "week"]):
        season, week = int(key[0]), int(key[1])
        needs = season if week >= SEASON_TO_DATE_FROM_WEEK else season - 1
        if needs not in seasons_with_usage:
            continue
        shares = shares_entering(player_weeks, season, week)
        merged = group.merge(shares, on=["team", "player_id"], how="left")
        merged["share"] = merged["share"].fillna(0.0)
        for team, burden in merged.groupby("team")["share"].sum().items():
            rows.append({"season": season, "week": week, "team": str(team),
                         "burden": float(burden)})
    return pd.DataFrame(rows, columns=["season", "week", "team", "burden"])
