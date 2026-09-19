"""Player ratings: what a player has actually done, per game and per touch.

The team ratings say who is good. This says who on those teams is doing the
work, which is the other half of the same question and the one a prop or a
DFS lineup is really about (docs/FOOTBALL_PAL.md).

Two families, because the data supports two:

* **Process, for quarterbacks.** EPA per dropback and CPOE (completion
  percentage over expected) come from the play-by-play, and they are the
  closest thing football has to an expected-outcome rating: CPOE asks what a
  throw of that difficulty completes at, not whether this one happened to be
  caught. NFL only — the college frame carries no per-player EPA.
* **Usage and efficiency, for everyone.** Carries, targets, receptions and
  yards per game, with yards per carry and per target, from the weekly box
  scores both leagues bank.

Every rate carries its volume, and a rate whose volume is below the
conventional minimum comes back **null rather than noisy**: two carries for
thirty yards is not a fifteen-yard-per-carry running back, and printing it
as one is how a table invents a breakout.
"""

from __future__ import annotations

import pandas as pd

from velocity.features.units import DEFAULT_MIN_GAMES, season_window

PLAYER_COLUMNS = [
    "league", "season_from", "season_to", "player_id", "player", "team",
    "position", "games", "dropbacks", "epa_per_dropback", "cpoe",
    "carries", "rush_yards", "yards_per_carry", "targets", "receptions",
    "rec_yards", "yards_per_target", "dk_points_per_game",
]

# The conventional floors under a rate. Below them the rate is null, not
# small: these are the volumes at which the number starts describing the
# player rather than the sample.
MIN_DROPBACKS = 50
MIN_CARRIES = 20
MIN_TARGETS = 15

# What the weekly box scores call the columns this reads. Both leagues agree
# on these names, which is why one function serves both.
_COUNTS = ("carries", "rush_yards", "targets", "receptions", "receiving_yards",
           "dk_points")


def _per_game(total: pd.Series, games: pd.Series) -> pd.Series:
    return (total / games.replace(0, pd.NA)).astype(float)


def _rate(numerator: pd.Series, denominator: pd.Series, floor: int) -> pd.Series:
    """A per-touch rate, null below ``floor`` touches."""
    safe = denominator.where(denominator >= floor)
    return (numerator / safe.replace(0, pd.NA)).astype(float)


def quarterback_process(
    plays: pd.DataFrame | None, window: list[int]
) -> pd.DataFrame:
    """EPA per dropback and CPOE per passer, over ``window``.

    Both are means over the passer's own dropbacks. A frame without the
    columns — every league but the NFL — returns empty rather than zeros.
    """
    needed = {"passer_player_id", "season"}
    if plays is None or plays.empty or not needed <= set(plays.columns):
        return pd.DataFrame(columns=["player_id", "dropbacks", "epa_per_dropback",
                                     "cpoe"])
    df = plays[plays["season"].isin(window)] if window else plays
    df = df.dropna(subset=["passer_player_id"])
    if df.empty:
        return pd.DataFrame(columns=["player_id", "dropbacks", "epa_per_dropback",
                                     "cpoe"])
    # Guaranteed rather than reached for: DataFrame.get returns Series | None
    # and to_numeric has no overload for None, so a frame missing the column
    # would fail on the type check rather than fall back.
    source = df.copy()
    if "qb_epa" not in source.columns:
        source["qb_epa"] = (source["epa"] if "epa" in source.columns
                            else float("nan"))
    if "cpoe" not in source.columns:
        source["cpoe"] = float("nan")
    frame = pd.DataFrame({
        "player_id": source["passer_player_id"].astype(str),
        "_epa": pd.to_numeric(source["qb_epa"], errors="coerce"),
        "_cpoe": pd.to_numeric(source["cpoe"], errors="coerce"),
    })
    grouped = frame.groupby("player_id", observed=True)
    out = grouped.agg(
        dropbacks=("_epa", "size"),
        epa_per_dropback=("_epa", "mean"),
        cpoe=("_cpoe", "mean"),
    ).reset_index()
    # Below the floor the numbers describe the sample, not the passer.
    thin = out["dropbacks"] < MIN_DROPBACKS
    out.loc[thin, ["epa_per_dropback", "cpoe"]] = float("nan")
    return out


def player_ratings(
    weeks: pd.DataFrame, league: str = "nfl", *, plays: pd.DataFrame | None = None,
    season: int | None = None, min_games: int = DEFAULT_MIN_GAMES,
) -> pd.DataFrame:
    """Per-player usage, efficiency and — for NFL passers — process.

    ``weeks`` is the weekly box-score frame both leagues bank. The window is
    :func:`velocity.features.units.season_window` over it, so a season too
    thin to read reaches back exactly as the team splits do, and every row
    states the window it used.
    """
    if weeks is None or weeks.empty or "player_id" not in weeks.columns:
        return pd.DataFrame(columns=PLAYER_COLUMNS)
    window = ([season] if season is not None
              else season_window(weeks, min_games, team_columns=("team",)))
    df = weeks[weeks["season"].isin(window)] if window else weeks
    if df.empty:
        return pd.DataFrame(columns=PLAYER_COLUMNS)

    df = df.copy()
    df["player_id"] = df["player_id"].astype(str)
    for column in _COUNTS:
        df[column] = (pd.to_numeric(df[column], errors="coerce").fillna(0.0)
                      if column in df.columns else 0.0)

    grouped = df.groupby("player_id", observed=True)
    out = grouped.agg(
        games=("game_id", "nunique"),
        carries=("carries", "sum"),
        rush_yards=("rush_yards", "sum"),
        targets=("targets", "sum"),
        receptions=("receptions", "sum"),
        rec_yards=("receiving_yards", "sum"),
        _dk=("dk_points", "sum"),
    ).reset_index()

    # Identity from the player's most recent week, so a midseason trade shows
    # the team he is on now rather than the one he has more rows with.
    order = ["season", "week"] if "week" in df.columns else ["season"]
    latest = (df.sort_values(order).groupby("player_id", observed=True).tail(1)
              .set_index("player_id"))
    for column, target in (("player_name", "player"), ("team", "team"),
                           ("position", "position")):
        known = (latest[column] if column in latest.columns
                 else pd.Series(dtype="object"))
        out[target] = out["player_id"].map(known).astype("object")

    out["yards_per_carry"] = _rate(out["rush_yards"], out["carries"], MIN_CARRIES)
    out["yards_per_target"] = _rate(out["rec_yards"], out["targets"], MIN_TARGETS)
    out["dk_points_per_game"] = _per_game(out["_dk"], out["games"])

    process = quarterback_process(plays, window)
    out = out.merge(process, on="player_id", how="left")
    for column in ("dropbacks", "epa_per_dropback", "cpoe"):
        if column not in out.columns:
            out[column] = float("nan")

    out["league"] = league
    out["season_from"] = min(window) if window else pd.NA
    out["season_to"] = max(window) if window else pd.NA
    # A row nobody can read: no touches, no dropbacks, no fantasy points. The
    # box score lists every player who dressed, and a table of zeroes buries
    # the ones who did something.
    touched = (out[["carries", "targets", "receptions"]].sum(axis=1) > 0)
    played = touched | out["dropbacks"].fillna(0).gt(0) | out["_dk"].gt(0)
    out = out[played]
    return (out[PLAYER_COLUMNS]
            .sort_values("dk_points_per_game", ascending=False, na_position="last")
            .reset_index(drop=True))
