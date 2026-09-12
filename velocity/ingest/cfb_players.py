"""College player-game lines from cfbfastR — the input NCAAF DFS never had.

The repo could price a college *game* and not a college *lineup*, because it
banked no player data at all: the plays frame carries eleven columns with no
player fields and the box scores are game level. CFBD serves player statistics
but needs an API key; **cfbfastR publishes the same substrate keyless**, on the
identical raw-CDN transport the WNBA (wehoop) and NCAAB (hoopR) verticals
already use, so it works from CI with no secret.

What it publishes is *play*-level: one row per play, with a column per role
naming the player who filled it. This module folds that into the one row per
player-game a projection needs, in the **NFL DFS vocabulary** — so
:func:`velocity.models.dfs_nfl.nfl_dk_points` and :class:`DfsNflModel` score
and fit it unchanged, which is also correct on the merits: DK's college
scoring is its NFL scoring.

Three things about the source are worth stating before trusting a number:

* **Touchdowns are attributed inconsistently.** On a passing touchdown the
  ``touchdown_player`` column names the passer 57% of the time and the
  receiver 43% — whichever ESPN's play text put first. So this never reads
  that column to decide *whose* touchdown it was. A play carrying a
  completion and a touchdown is a passing touchdown for the passer and a
  receiving touchdown for the receiver, which is what football says; a play
  carrying a rush and a touchdown is a rushing touchdown. Verified safe: an
  interception play never carries a completion (so a pick-six cannot become a
  passing touchdown) and a fumble play never carries a touchdown (so a
  fumble return cannot become a rushing one).
* **A target is only recorded on an incompletion.** ``target_player`` is the
  intended receiver on an incomplete pass and is empty on every completion,
  so a target is a reception plus an incompletion aimed at him.
* **It is derived from ESPN's play text, and it is not exhaustive.**
  Interceptions in particular come in at ~1.3 a game against a real ~2.4:
  the text names the interceptor reliably on a return touchdown and less so
  otherwise. That biases the −1 interception term downward. Fumbles are
  skipped entirely — the frame names the fumbler but not the recovering team,
  so a lost fumble cannot be told from one the offense got back, and a guess
  would be worse than the omission. Both leave projections marginally high by
  a fraction of a point, which is documented rather than silently absorbed.

Pure functions of frames plus one network loader; offline-testable.
"""

from __future__ import annotations

import io
import time
import urllib.request
from collections.abc import Iterable

import pandas as pd

CFBFASTR_PLAYER_STATS_URL = (
    "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/"
    "player_stats/parquet/player_stats_{season}.parquet"
)
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) velocity-datasets"

# The DK stat columns a college skill player can accumulate. Kicking is absent
# because DK's college classic roster has no kicker slot (CFB_CLASSIC), and
# fumbles because the source cannot say whether one was lost.
STAT_COLUMNS = (
    "attempts", "pass_yards", "pass_tds", "interceptions",
    "carries", "rush_yards", "rush_tds",
    "targets", "receptions", "receiving_yards", "receiving_tds",
)
IDENTITY_COLUMNS = ("season", "week", "game_id", "player_id", "player_name",
                    "team", "opponent", "position")

# DK lists college tight ends as wide receivers and has no defensive slot, so
# the vocabulary is the three the roster spec actually fills.
CFB_POSITIONS = ("QB", "RB", "WR")


def _role(
    plays: pd.DataFrame, id_column: str, name_column: str, stats: dict[str, pd.Series]
) -> pd.DataFrame:
    """The rows where one role was filled, as player-keyed stat contributions."""
    mask = plays[id_column].notna()
    if not mask.any():
        return pd.DataFrame()
    out = pd.DataFrame({
        "game_id": plays.loc[mask, "game_id"].astype(str),
        "player_id": plays.loc[mask, id_column].astype(str),
        "player_name": plays.loc[mask, name_column].astype(str),
        "team": plays.loc[mask, "team"].astype(str),
        "opponent": plays.loc[mask, "opponent"].astype(str),
        "season": plays.loc[mask, "season"],
        "week": plays.loc[mask, "week"],
    })
    for column, values in stats.items():
        out[column] = values[mask].to_numpy()
    return out


def player_games(plays: pd.DataFrame) -> pd.DataFrame:
    """A cfbfastR play-level frame folded to one row per player-game.

    Returns the identity columns plus :data:`STAT_COLUMNS` and ``dk_points``
    — the DK line as it was actually scored, milestone bonuses included.
    """
    columns = [*IDENTITY_COLUMNS, *STAT_COLUMNS, "dk_points"]
    if plays.empty:
        return pd.DataFrame(columns=columns)
    frame = plays.copy()
    frame["game_id"] = frame["game_id"].astype(str)

    def num(column: str) -> pd.Series:
        if column not in frame.columns:
            return pd.Series(0.0, index=frame.index)
        return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)

    def present(column: str) -> pd.Series:
        return (frame[column].notna() if column in frame.columns
                else pd.Series(False, index=frame.index)).astype(float)

    scored = present("touchdown_player_id")
    one = pd.Series(1.0, index=frame.index)

    parts = [
        # The passer: a completion is an attempt and yards; a completion on a
        # scoring play is his passing touchdown, whoever the source named.
        _role(frame, "completion_player_id", "completion_player",
              {"attempts": one, "pass_yards": num("completion_yds"),
               "pass_tds": scored}),
        _role(frame, "incompletion_player_id", "incompletion_player",
              {"attempts": one}),
        _role(frame, "interception_thrown_player_id", "interception_thrown_player",
              {"attempts": one, "interceptions": one}),
        _role(frame, "rush_player_id", "rush_player",
              {"carries": one, "rush_yards": num("rush_yds"), "rush_tds": scored}),
        # The receiver: a reception is a target too, and the intended man on
        # an incompletion is the only other place a target is recorded.
        _role(frame, "reception_player_id", "reception_player",
              {"receptions": one, "targets": one,
               "receiving_yards": num("reception_yds"), "receiving_tds": scored}),
        _role(frame, "target_player_id", "target_player", {"targets": one}),
    ]
    stacked = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    for column in STAT_COLUMNS:
        if column not in stacked.columns:
            stacked[column] = 0.0
        stacked[column] = stacked[column].fillna(0.0)

    grouped = stacked.groupby(["game_id", "player_id"], as_index=False).agg(
        player_name=("player_name", "first"), team=("team", "first"),
        opponent=("opponent", "first"), season=("season", "first"),
        week=("week", "first"),
        **{c: (c, "sum") for c in STAT_COLUMNS},
    )
    grouped["position"] = infer_positions(grouped)

    from velocity.models.dfs_nfl import nfl_dk_points

    grouped["dk_points"] = nfl_dk_points(grouped)
    return grouped[columns].reset_index(drop=True)


def infer_positions(player_games_frame: pd.DataFrame) -> pd.Series:
    """Each player's position, read off how his team used him.

    The source ships no position column, and a roster scrape would be a second
    feed to keep alive for something the usage already states plainly: a
    player who throws is a quarterback, one who is handed the ball is a
    running back, one who is thrown to is a receiver. Ties go to the rarer
    role, which is the one that identifies him — a quarterback who also runs
    is still a quarterback.

    Judged on a player's WHOLE sample rather than one game, so a wildcat snap
    or a halfback pass does not reclassify anyone.
    """
    if player_games_frame.empty:
        return pd.Series(dtype=str)
    frame = player_games_frame
    totals = frame.groupby("player_id")[["attempts", "carries", "targets"]].sum()
    # Scaled so the rarer role wins a close call: a back who takes twenty
    # carries and throws once is a back, a quarterback who runs ten times and
    # throws twenty is not.
    ranked = pd.DataFrame({
        "QB": totals["attempts"] * 3.0,
        "RB": totals["carries"] * 1.0,
        "WR": totals["targets"] * 1.0,
    })
    best = ranked.idxmax(axis=1).where(ranked.max(axis=1) > 0, "")
    return frame["player_id"].map(best).fillna("").astype(str)


def fetch_player_games(season: int) -> tuple[pd.DataFrame, float]:  # pragma: no cover - network
    """One season of cfbfastR player plays, folded, with its coverage.

    The coverage rides along because it can only be measured on the raw frame
    — the folded one carries no score to check the attribution against.
    """
    req = urllib.request.Request(CFBFASTR_PLAYER_STATS_URL.format(season=season),
                                 headers={"User-Agent": _USER_AGENT})
    for attempt, delay in enumerate((0, 5, 15)):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310
                raw = pd.read_parquet(io.BytesIO(resp.read()))
                return player_games(raw), season_coverage(raw)
        except Exception:  # noqa: BLE001 - retried; the last attempt raises
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")


def load_player_games(seasons: Iterable[int]) -> pd.DataFrame:  # pragma: no cover - network
    """Fetch and fold several seasons, skipping any the release lacks."""
    frames = []
    for season in seasons:
        try:
            frame, _covered = fetch_player_games(season)
            frames.append(frame)
        except Exception as exc:  # noqa: BLE001 - a missing season never blocks
            print(f"cfbfastR player stats {season} unavailable ({exc})")
    return (pd.concat(frames, ignore_index=True) if frames
            else player_games(pd.DataFrame()))


def season_coverage(plays: pd.DataFrame) -> float:
    """The share of team-games whose final score this release's scoring explains.

    The release fills progressively, and a season it has not finished
    attributing looks exactly like a season in which nobody scored — so a
    projection fitted on it would price every player at nothing. This is the
    check that tells the two apart: a team-game is covered when seven points
    per touchdown plus three per field goal lands within a point of the final
    score the frame itself carries.

    Measured on the RAW play frame, which is the only place the score lives.
    The banked seasons come in at 0.82 (2023) and 0.75 (2024); a season below
    about half cannot price a lineup, and the build script says so.
    """
    if plays.empty or "touchdown_player_id" not in plays.columns:
        return 0.0
    finals = plays.groupby(["game_id", "team"])["team_score"].max()
    if finals.empty:
        return 0.0
    tds = plays[plays["touchdown_player_id"].notna()].groupby(["game_id", "team"]).size()
    explained = 7.0 * tds.reindex(finals.index).fillna(0.0)
    if "field_goal_made_player_id" in plays.columns:
        fgs = (plays[plays["field_goal_made_player_id"].notna()]
               .groupby(["game_id", "team"]).size())
        explained = explained + 3.0 * fgs.reindex(finals.index).fillna(0.0)
    return float(((finals - explained).abs() <= 1.0).mean())
