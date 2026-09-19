"""Unit splits: how each team's pass and rush games fare, on offense and defense.

The ratings fit answers "how good is this team" in one number per side. That
number is what prices a game, and it deliberately hides the *shape*: a defense
that is stout against the run and porous against the pass rates the same as an
evenly average one. The shape is what a matchup surface is for — the football
analogue of a batter-versus-pitcher page (docs/FOOTBALL_PAL.md).

Two deliberate limits, both stated rather than papered over:

* **This is a descriptive split, not the fitted model.** The ridge fit in
  :mod:`velocity.features.team` is what projects games; nothing here reaches
  a price. These numbers exist to be read.
* **The opponent adjustment is one pass, not a fit.** Each team's per-play
  average is corrected by the average of the units it actually faced, which
  removes most of the schedule effect and is honest about being a single
  iteration. A ridge fit would solve offense and defense simultaneously; this
  does not, and a team whose opponents themselves played nobody keeps a
  little of their softness.
"""

from __future__ import annotations

import pandas as pd

from velocity.features.team import scrimmage_plays

# The two phases a snap can be. Everything else that survives the scrimmage
# filter — a fumble recovery, a safety — belongs to neither and is dropped
# rather than assigned to one.
PHASES = ("pass", "rush")

UNIT_COLUMNS = [
    "league", "season_from", "season_to", "games", "team", "side", "phase",
    "plays", "epa_per_play", "epa_adjusted", "success_rate",
]

# A team needs this many games before its own season says anything. Below it
# the window reaches back a season, because the alternative is worse than
# mixing last year's roster in: with one game played, a team's unit IS its
# single opponent's mirror image, and the opponent adjustment then collapses
# it to exactly the league average — arithmetically correct and completely
# uninformative. Four is the point where a team has played enough different
# opponents for the correction to carry information.
DEFAULT_MIN_GAMES = 4


def classify_phase(kind: pd.Series, league: str = "nfl") -> pd.Series:
    """Play labels → ``pass`` / ``rush`` / NA.

    The NFL labels a snap ``pass`` or ``run`` and counts a sack as a pass,
    which is what a dropback is. College writes prose — "pass reception",
    "rushing touchdown", "pass interception return" — so the phase is read
    out of the words, rush first. No label contains both, and anything
    carrying neither (a fumble recovery, a safety) comes back NA so the
    caller drops it instead of guessing.
    """
    text = kind.astype("string").str.lower()
    if league == "nfl":
        return text.map({"pass": "pass", "run": "rush"}).astype("string")
    phase = pd.Series(pd.NA, index=text.index, dtype="string")
    is_rush = text.str.contains("rush", na=False)
    # A sack and an interception are both pass plays; the dropback happened.
    is_pass = text.str.contains("pass|sack|interception", regex=True, na=False)
    phase[is_pass & ~is_rush] = "pass"
    phase[is_rush] = "rush"
    return phase


def _side_means(
    plays: pd.DataFrame, team_col: str, side: str
) -> pd.DataFrame:
    """Per-team, per-phase raw means for one side of the ball."""
    grouped = plays.groupby([team_col, "phase"], observed=True)
    out = grouped.agg(
        plays=("epa", "size"),
        epa_per_play=("epa", "mean"),
        success_rate=("success", "mean"),
    ).reset_index()
    return out.rename(columns={team_col: "team"}).assign(side=side)


def season_window(
    plays: pd.DataFrame, min_games: int = DEFAULT_MIN_GAMES
) -> list[int]:
    """The newest seasons that together give teams ``min_games`` apiece.

    A matchup page is about this season, and last season's defense is a
    different unit — so the window starts at the newest season and only
    reaches back when that season cannot support a read. In week three a
    team has played once, and one game is not a unit; two seasons of a
    changed roster beats one game of nothing.
    """
    if "season" not in plays.columns:
        return []
    seasons = sorted({int(s) for s in plays["season"].dropna().unique()}, reverse=True)
    if "game_id" not in plays.columns or "posteam" not in plays.columns:
        return seasons[:1]
    chosen: list[int] = []
    for season in seasons:
        chosen.append(season)
        per_team = games_played(plays[plays["season"].isin(chosen)])
        if not per_team.empty and float(per_team.median()) >= min_games:
            break
    return chosen


def games_played(plays: pd.DataFrame) -> pd.Series:
    """Distinct games each team appears in, on **either** side of the ball.

    Counting only the snaps a team ran would credit it with no games at all
    in a frame where it never had the ball, which is how a defense came back
    showing zero games played.
    """
    if not {"game_id", "posteam", "defteam"} <= set(plays.columns):
        return pd.Series(dtype="int64")
    sides = [
        plays[[column, "game_id"]].rename(columns={column: "team"})
        for column in ("posteam", "defteam")
    ]
    appearances = pd.concat(sides, ignore_index=True).dropna(subset=["team"])
    return appearances.groupby("team", observed=True)["game_id"].nunique()


def unit_splits(
    plays: pd.DataFrame, league: str = "nfl", *, season: int | None = None,
    min_plays: int = 25, min_games: int = DEFAULT_MIN_GAMES,
) -> pd.DataFrame:
    """Per-team pass and rush splits, both sides, raw and opponent-adjusted.

    The window is :func:`season_window` — the newest season, widened only
    when it is too thin to read — and every row states it, so a reader is
    never left guessing whether a number is this year's. ``season`` pins a
    single season instead. ``min_plays`` drops cells too thin to read; the
    survivors carry their ``plays`` and ``games`` counts.

    ``epa_per_play`` is the raw average. ``epa_adjusted`` corrects it by the
    strength of the units actually faced, centered on the league's own
    per-phase mean, so zero is average and an offense's positive number is
    good while a defense's negative number is good.
    """
    if plays.empty or not {"posteam", "defteam", "epa"} <= set(plays.columns):
        return pd.DataFrame(columns=UNIT_COLUMNS)
    df = scrimmage_plays(plays, league)
    if "season" in df.columns:
        window = [season] if season is not None else season_window(df, min_games)
        df = df[df["season"].isin(window)]
    else:
        window = []
    df = df.assign(phase=classify_phase(df["play_type"], league))
    df = df.dropna(subset=["phase", "posteam", "defteam", "epa"])
    if df.empty:
        return pd.DataFrame(columns=UNIT_COLUMNS)
    if "success" not in df.columns:
        df = df.assign(success=float("nan"))

    offense = _side_means(df, "posteam", "offense")
    defense = _side_means(df, "defteam", "defense")
    league_mean = df.groupby("phase", observed=True)["epa"].mean()

    # One pass of opponent adjustment. An offense's number is corrected by
    # how good the defenses it faced were, and vice versa: subtract the
    # average opponent's deviation from the league's own per-phase mean, so
    # facing tough units moves a team's number up.
    def adjust(side_means: pd.DataFrame, team_col: str, opponent_col: str,
               opponent_means: pd.DataFrame) -> pd.DataFrame:
        """``side_means`` corrected by the average opponent unit it faced."""
        faced = df[[team_col, opponent_col, "phase"]].merge(
            opponent_means[["team", "phase", "epa_per_play"]].rename(
                columns={"team": opponent_col, "epa_per_play": "_opp"}),
            on=[opponent_col, "phase"], how="left",
        )
        # Deviation from the league's own per-phase mean, so an average
        # schedule corrects by nothing and the units stay EPA per play.
        faced["_dev"] = faced["_opp"] - faced["phase"].map(league_mean)
        by_team = (faced.groupby([team_col, "phase"], observed=True)["_dev"]
                   .mean().rename("_dev").reset_index()
                   .rename(columns={team_col: "team"}))
        merged = side_means.merge(by_team, on=["team", "phase"], how="left")
        merged["epa_adjusted"] = (merged["epa_per_play"]
                                  - merged["_dev"].fillna(0.0))
        return merged.drop(columns=["_dev"])

    offense = adjust(offense, "posteam", "defteam", defense)
    defense = adjust(defense, "defteam", "posteam", offense)

    out = pd.concat([offense, defense], ignore_index=True)
    out = out[out["plays"] >= min_plays]
    out["league"] = league
    out["season_from"] = min(window) if window else pd.NA
    out["season_to"] = max(window) if window else pd.NA
    out["games"] = out["team"].map(games_played(df)).fillna(0).astype("int64")
    out["team"] = out["team"].astype(str)
    out["phase"] = out["phase"].astype(str)
    return (out[UNIT_COLUMNS]
            .sort_values(["side", "phase", "epa_adjusted"], ascending=[True, True, False])
            .reset_index(drop=True))
