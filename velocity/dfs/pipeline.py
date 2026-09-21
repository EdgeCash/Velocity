"""DFS pipeline — salary snapshot + FantasyPros projections → the best lineup.

The pure glue between the collectors and the optimizer: pick the main slate
out of a DK salary snapshot, score the FantasyPros long frame into expected
DK points, join, and solve. Offline-testable end to end; the CLI wrapper
(``scripts/build_dfs_lineup.py``) only adds file IO and the card render.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pandas as pd

from velocity.dfs.optimizer import (
    CFB_CLASSIC,
    MLB_CLASSIC,
    NFL_CLASSIC,
    SALARY_CAP,
    WNBA_CLASSIC,
    Lineup,
    RosterSpec,
    build_lineup,
    lineup_pool,
)
from velocity.dfs.scoring import (
    dk_expected_points,
    dk_expected_points_mlb,
    dk_expected_points_ncaaf,
    dk_expected_points_wnba,
)

# The leagues the optimizer can price: roster spec + projections scorer.
# Other leagues bank salary history (SPORT_CODES) but build no lineup yet.
# WNBA's scorer takes the league's own banked player boxes rather than a
# projection service's frame — none serves the league for free — which is the
# same arrangement MLB has with its statsapi snapshot.
LEAGUE_SPECS = {
    "nfl": (NFL_CLASSIC, dk_expected_points),
    # College prices from its own banked player-games: FantasyPros serves no
    # college players at all, so the previous scorer filtered its frame to
    # zero rows and the builder exited cleanly every run.
    "ncaaf": (CFB_CLASSIC, dk_expected_points_ncaaf),
    "mlb": (MLB_CLASSIC, dk_expected_points_mlb),
    "wnba": (WNBA_CLASSIC, dk_expected_points_wnba),
}


@dataclass(frozen=True)
class LineupRun:
    """One solved slate: the lineup plus the context the card and logs state."""

    lineup: Lineup | None
    draft_group_id: str
    n_games: int
    n_salaried: int  # players on the DK board for the group
    n_pool: int  # players surviving the projection join
    # The priced board itself — every draftable the model has a number for,
    # not only the nine the optimizer rostered. The solve throws this away by
    # construction, and it is the more useful half for research: a lineup
    # answers "what should I play", the pool answers "what is this player
    # worth today", which is the question the site's Players view asks
    # (docs/FOOTBALL_PAL.md). Defaulted so an unsolved run still constructs.
    pool: pd.DataFrame = field(default_factory=pd.DataFrame)


def is_season_long(fp: pd.DataFrame) -> bool:
    """True when a FantasyPros frame carries season-long (week 0) projections.

    Season totals collapse to absurd weekly numbers (a 372-point QB), so
    weekly consumers — the DFS lineup and the prop sim — must refuse them
    rather than price nonsense. Proven live: the first demo run built a
    1,761-point "lineup" from a week-0 snapshot.
    """
    if fp.empty or "week" not in fp.columns:
        return False
    weeks = pd.to_numeric(fp["week"], errors="coerce").fillna(0)
    return bool((weeks == 0).all())


@dataclass(frozen=True)
class SlateInfo:
    """One priceable slate grouping: DK's identity for a draft group."""

    draft_group_id: str
    start: pd.Timestamp | None  # slate lock (tz-naive UTC)
    suffix: str  # DK's grouping label: "Turbo" / "Night" / "Early" / ""
    n_games: int


def classic_slates(salaries: pd.DataFrame) -> list[SlateInfo]:
    """Every solvable classic slate grouping in a snapshot, in lock order.

    DK posts several classic boards per day — the main slate plus Early /
    Night / Turbo groupings — as separate draft groups sharing one contest
    type. The main slate (most games) anchors which ``contest_type_id`` is
    "classic" for this board, then every multi-game group of that type is a
    slate worth solving. Snapshots banked before the type column existed
    fall back to the multi-game filter alone (showdowns are single-game, and
    salary-less formats like Tiers never survive normalization anyway).
    """
    main = main_slate_group(salaries)
    if main is None:
        return []
    by_group = salaries.groupby(salaries["draft_group_id"].astype(str)).agg(
        games=("competition", "nunique"),
        start=("slate_start", "first") if "slate_start" in salaries.columns
        else ("kickoff", "min"),
        suffix=("suffix", "first") if "suffix" in salaries.columns
        else ("draft_group_id", lambda _s: ""),
        contest_type=("contest_type_id", "first")
        if "contest_type_id" in salaries.columns
        else ("draft_group_id", lambda _s: None),
    )
    multi = by_group[by_group["games"] >= 2]
    classic_type = by_group.loc[str(main), "contest_type"]
    if classic_type is not None and not pd.isna(classic_type):
        multi = multi[multi["contest_type"] == classic_type]
    multi = multi.sort_values(["start", "games"], ascending=[True, False])
    return [
        SlateInfo(
            draft_group_id=str(gid),
            start=None if pd.isna(row["start"]) else pd.Timestamp(row["start"]),
            suffix="" if pd.isna(row["suffix"]) else str(row["suffix"]),
            n_games=int(row["games"]),
        )
        for gid, row in multi.iterrows()
    ]


def showdown_slates(salaries: pd.DataFrame, league: str) -> list[SlateInfo]:
    """Every Showdown Captain Mode board in a snapshot, in lock order.

    Showdown groups are identified by DK's own format label — the
    ``game_type`` name when the snapshot carries it, else the numeric
    ``contest_type_id`` (MLB 114, NFL 96, CFB 95). Not by "one game": several
    DK formats are single-game and only this one is captain-mode, and DK also
    runs a simulated look-alike ("Madden Showdown Captain Mode") that must
    not be priced off real-player projections. A snapshot with neither column
    carries no showdown boards rather than guessing at them.
    """
    from velocity.dfs.showdown import SHOWDOWN_CONTEST_TYPES, SHOWDOWN_GAME_TYPE

    if league not in SHOWDOWN_CONTEST_TYPES or salaries.empty:
        return []
    if "game_type" in salaries.columns and salaries["game_type"].notna().any():
        board = salaries[salaries["game_type"].astype(str) == SHOWDOWN_GAME_TYPE]
    elif "contest_type_id" in salaries.columns:
        board = salaries[
            pd.to_numeric(salaries["contest_type_id"], errors="coerce")
            == SHOWDOWN_CONTEST_TYPES[league]
        ]
    else:
        return []
    if board.empty:
        return []
    by_group = board.groupby(board["draft_group_id"].astype(str)).agg(
        games=("competition", "nunique"),
        start=("slate_start", "first") if "slate_start" in board.columns
        else ("kickoff", "min"),
        game=("competition", "first"),
    )
    by_group = by_group.sort_values(["start", "games"])
    return [
        SlateInfo(
            draft_group_id=str(gid),
            start=None if pd.isna(row["start"]) else pd.Timestamp(row["start"]),
            # The matchup is the label a showdown board wants ("LAD @ ATL"),
            # not DK's Early/Night suffix — one game has no grouping.
            suffix="" if pd.isna(row["game"]) else str(row["game"]),
            n_games=int(row["games"]),
        )
        for gid, row in by_group.iterrows()
    ]


def solve_showdown(
    salaries: pd.DataFrame,
    fp: pd.DataFrame,
    *,
    draft_group: str,
    league: str = "mlb",
    points: pd.DataFrame | None = None,
) -> LineupRun:
    """Solve one Showdown Captain Mode board (docs/DFS_FORMATS.md).

    Same contract as :func:`solve_slate` — an unsolvable board returns a run
    with ``lineup=None``. The doubled DK board (every player priced twice,
    captain and flex) is collapsed first; projections are the flex-slot
    numbers, and the captain's 1.5x is applied by the optimizer.
    """
    from velocity.dfs.showdown import SHOWDOWN_SPECS, build_showdown, showdown_board

    spec = SHOWDOWN_SPECS.get(league)
    if spec is None:
        return LineupRun(None, str(draft_group), 0, 0, 0)
    board = salaries[salaries["draft_group_id"].astype(str) == str(draft_group)]
    board = showdown_board(board)
    board = eligible_board(normalize_positions(board, spec), spec)
    if points is None:
        # The league's own scorer, exactly as the classic path picks it: the
        # old mlb-or-FantasyPros choice sent the college bank (no ``stat``
        # column) through the FantasyPros scorer, which raised KeyError and,
        # because the showdown boards solve first, cost NCAAF its classic
        # slates too (2026-09-18).
        _spec, scorer = LEAGUE_SPECS.get(league, (None, dk_expected_points))
        points = scorer(fp)
    pool = lineup_pool(board, points)
    lineup = build_showdown(pool, spec=spec) if not pool.empty else None
    return LineupRun(
        lineup=lineup,
        draft_group_id=str(draft_group),
        n_games=int(board["competition"].nunique()) if not board.empty else 0,
        n_salaried=len(board),
        n_pool=len(pool),
        pool=pool,
    )


def game_time_ct(kickoff: object) -> str:
    """A game start as the cards state times ("6:40P CT"), or an em-dash.

    Central time via the real tz database (a fixed UTC offset would drift an
    hour across DST), from the tz-naive-UTC kickoffs the DK snapshot banks.
    """
    try:
        stamp = pd.Timestamp(kickoff)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    if pd.isna(stamp):
        return "—"
    central = stamp.tz_localize("UTC").tz_convert("America/Chicago")
    return central.strftime("%-I:%M%p CT").replace("AM", "A").replace("PM", "P")


def slate_label_ct(slate: SlateInfo) -> str:
    """"Mon 5:40 PM CT (Turbo) · 3 games" — the slate as DK's lobby names it.

    Central time to match every other timestamp the cards state. Empty when
    the snapshot carried no slate start (a --draft-group pin, old files).
    """
    bits: list[str] = []
    if slate.start is not None:
        central = slate.start.tz_localize("UTC").tz_convert("America/Chicago")
        bits.append(central.strftime("%a %-I:%M %p CT"))
    if slate.suffix:
        bits.append(f"({slate.suffix})")
    if slate.n_games:
        noun = "game" if slate.n_games == 1 else "games"
        bits.append(f"· {slate.n_games} {noun}" if bits
                    else f"{slate.n_games} {noun}")
    return " ".join(bits)


def draft_rank_groups(salaries: pd.DataFrame) -> set[str]:
    """Draft groups whose "salary" is a draft rank, not dollars.

    DK posts Snake and Best Ball boards in the same lobby as the salary-cap
    ones, and normalization cannot tell them apart: both carry a ``salary``
    column of plain integers. On a draft board that integer is the pick order,
    so the column reads 1, 2, 3 … N over exactly N players.

    That signature — minimum 1, maximum equal to the row count — is what this
    matches, rather than DK's format names, which are marketing and change.
    No salary-cap board can collide with it: the cheapest DK has ever priced
    a player is $200, and salaries repeat across players besides.

    This matters because ``main_slate_group`` picks the group spanning the
    most games, and Best Ball's "W3-W17 Sit & Go" spans fifteen weeks. On
    2026-09-20 it beat the real main slate (16 competitions against 13) and
    anchored the whole classic selection onto a board of draft ranks: every
    lineup solve failed ("1444 salaried, 758 projected: no solvable lineup"),
    and the pool that was banked priced Bijan Robinson at a salary of 1,
    which made his points-per-$1,000 read 23,830 and put him top of the
    board's "best value" list.
    """
    if salaries.empty or "salary" not in salaries.columns:
        return set()
    flagged: set[str] = set()
    for gid, board in salaries.groupby(salaries["draft_group_id"].astype(str)):
        values = pd.to_numeric(board["salary"], errors="coerce").dropna()
        if values.empty:
            continue
        if values.min() == 1 and values.max() == len(values):
            flagged.add(str(gid))
    return flagged


def drop_draft_rank_boards(salaries: pd.DataFrame) -> pd.DataFrame:
    """``salaries`` without the Snake / Best Ball boards. See above."""
    flagged = draft_rank_groups(salaries)
    if not flagged:
        return salaries
    keep = ~salaries["draft_group_id"].astype(str).isin(flagged)
    return salaries[keep].reset_index(drop=True)


def main_slate_group(salaries: pd.DataFrame) -> str | None:
    """The draft group that looks like the classic main slate.

    Largest distinct-game count wins (the main slate spans the most games);
    ties break to the most salaried players, then lowest id for determinism.
    """
    if salaries.empty:
        return None
    by_group = salaries.groupby(salaries["draft_group_id"].astype(str)).agg(
        games=("competition", "nunique"), players=("player_id", "size")
    )
    top = by_group.sort_values(["games", "players"], ascending=False).iloc[0]
    winners = by_group[
        (by_group["games"] == top["games"]) & (by_group["players"] == top["players"])
    ]
    return str(sorted(winners.index)[0])


def normalize_positions(board: pd.DataFrame, spec: object) -> pd.DataFrame:
    """Map DK position strings onto the spec's slot vocabulary.

    DK's MLB board spells pitchers ``SP``/``RP`` (both fill the P slots) and
    multi-eligible fielders as alphabetical combos (``2B/SS``, ``OF/SS``);
    those price at the first listed position — every listed position is DK-
    legal, so the lineup stays valid, occasionally sub-optimal.

    DK's WNBA board runs two positions, guard and forward, and lists dual
    eligibility the same combo way (``G/F``). A centre is a forward there, so
    the box scores' ``C`` — which is ESPN's vocabulary, not DK's — folds into
    ``F`` rather than falling out of the pool. Football boards pass through
    untouched.
    """
    name = str(getattr(spec, "name", ""))
    if name.startswith("mlb"):
        pos = (
            board["position"].astype(str).str.upper().str.strip()
            .str.split("/").str[0]
            .replace({"SP": "P", "RP": "P"})
        )
        return board.assign(position=pos)
    if name.startswith("wnba"):
        pos = (
            board["position"].astype(str).str.upper().str.strip()
            .str.split("/").str[0]
            .replace({"C": "F"})
        )
        return board.assign(position=pos)
    return board


# DK MLB classic legality: at most 5 HITTERS from one team (pitchers exempt).
MLB_MAX_HITTERS_PER_TEAM = 5


def _mlb_cap_violation(lineup: Lineup) -> str | None:
    """The team with too many rostered hitters, or None when legal."""
    counts: dict[str, int] = {}
    for slot in lineup.slots:
        if slot.position != "P" and slot.team:
            counts[slot.team] = counts.get(slot.team, 0) + 1
    over = {t: n for t, n in counts.items() if n > MLB_MAX_HITTERS_PER_TEAM}
    return max(over, key=lambda t: over[t]) if over else None


def solve_mlb_lineup(pool: pd.DataFrame, spec: RosterSpec,
                     cap: int = SALARY_CAP) -> Lineup | None:
    """Exact solve + iterative cuts for the ≤5-hitters-per-team rule.

    The knapsack has no team dimension, so the rule is enforced by cutting:
    when the optimum stacks six hitters from one team, the cheapest-value
    hitter of that team leaves the pool and the slate re-solves. Each cut
    only removes a player the violating optimum used, so this terminates
    quickly; the result is legal, if occasionally a hair off the true
    constrained optimum.
    """
    pool = pool.copy()
    for _ in range(20):
        lineup = build_lineup(pool, cap=cap, spec=spec)
        if lineup is None:
            return None
        team = _mlb_cap_violation(lineup)
        if team is None:
            return lineup
        hitters = [s for s in lineup.slots
                   if s.team == team and s.position != "P"]
        cut = min(hitters, key=lambda s: s.points / max(s.salary, 1))
        pool = pool[pool["player_name"] != cut.player_name]
    return None


# Statuses that mean the player is not taking the field — never rosterable.
# DTD/Q (questionable) deliberately stay in: most of them play.
_OUT_STATUSES = frozenset({"IL", "IR", "O", "OUT", "NA", "SUSP"})


def eligible_board(board: pd.DataFrame, spec: object) -> pd.DataFrame:
    """Drop players who cannot take the field before the solve sees them.

    Three live-proven rules (the first Turbo lineup rostered a non-probable
    prospect and an IL'd reliever at P; a later board hid the night's actual
    starter):

    * anyone whose DK status marks them out (IL/OUT/...) leaves the pool;
    * **unless** DK also flags him probable — the probable marker is a
      statement about tonight's game, the status a roster designation DK
      has not cleared yet, and the game-level fact wins. Live proof: DK
      carried Tyler Glasnow as ``IL`` on the 8/25 LAD@ATL board while
      flagging him probable, and statsapi had him announced as the starter;
    * on an MLB board, the P pool keeps ONLY DK's flagged probables — DK
      lists every rostered pitcher, but a non-probable never starts.

    Call after :func:`normalize_positions` (the P check reads the normalized
    slot vocabulary). Snapshots banked before the ``probable`` column existed
    pass through the status filter alone.
    """
    probable = (
        board["probable"].fillna(False).astype(bool) if "probable" in board.columns
        else pd.Series(False, index=board.index)
    )
    if "status" in board.columns:
        status = board["status"].astype(str).str.upper().str.strip()
        board = board[~status.isin(_OUT_STATUSES) | probable]
        probable = probable.loc[board.index]
    # Any MLB format (classic or showdown) — both specs name themselves
    # "mlb_*", so the rule follows the sport rather than one roster object.
    if str(getattr(spec, "name", "")).startswith("mlb") and "probable" in board.columns:
        is_p = board["position"].astype(str) == "P"
        board = board[~is_p | probable]
    return board


def solve_slate(
    salaries: pd.DataFrame,
    fp: pd.DataFrame,
    *,
    draft_group: str | None = None,
    spec: RosterSpec = NFL_CLASSIC,
    scorer: object = None,
    points: pd.DataFrame | None = None,
) -> LineupRun:
    """Solve one draft group's optimal lineup from raw frames.

    ``salaries`` is the collector's normalized frame (any number of draft
    groups — the main slate is auto-picked unless ``draft_group`` pins one);
    ``fp`` is the FantasyPros long frame; ``spec`` the contest format;
    ``scorer`` the FP-frame → per-player-points function (defaults to the
    NFL classic scorer). ``points`` supplies an already-computed projection
    frame and bypasses ``scorer`` entirely — the path the contextual MLB
    model uses, since it reads the box-score banks rather than a stat frame.
    An
    unsolvable slate (no group, or an infeasible pool) returns a run with
    ``lineup=None`` rather than raising — an empty board is a state, not an
    error.
    """
    group = draft_group or main_slate_group(salaries)
    if group is None:
        return LineupRun(None, "", 0, 0, 0)
    board = salaries[salaries["draft_group_id"].astype(str) == str(group)]
    board = eligible_board(normalize_positions(board, spec), spec)
    if points is None:
        score = scorer if callable(scorer) else dk_expected_points
        points = score(fp)
    pool = lineup_pool(board, points)
    if pool.empty:
        lineup = None
    elif spec is MLB_CLASSIC:
        lineup = solve_mlb_lineup(pool, spec)
    else:
        lineup = build_lineup(pool, spec=spec)
    return LineupRun(
        lineup=lineup,
        draft_group_id=str(group),
        n_games=int(board["competition"].nunique()),
        n_salaried=len(board),
        n_pool=len(pool),
        pool=pool,
    )


# What the pool frame carries. Everything the DK board said about a player,
# the model's number for them, and the one derived column a DFS reader
# actually sorts on.
POOL_COLUMNS = [
    "player_name", "position", "team", "salary", "points", "value",
    "rostered", "competition", "kickoff", "status", "probable",
    "draft_group_id",
]


def pool_frame(run: LineupRun) -> pd.DataFrame:
    """The slate's priced pool as a persistable frame (empty when there is none).

    ``value`` is DK's own convention — projected points per $1,000 of salary —
    computed here rather than in the browser so every surface reads the same
    number. ``rostered`` marks the players the optimizer actually took, so the
    lineup is readable as a subset of the pool rather than a separate list.
    A salary of zero (DK's salary-free Tiers and Single Stat boards) has no
    value per dollar, and says so with a null rather than an infinity.
    """
    pool = run.pool
    if pool is None or pool.empty:
        return pd.DataFrame(columns=POOL_COLUMNS)
    frame = pool.copy()
    # ``lineup_pool`` always returns both, but the column is guaranteed here
    # rather than reached for with ``.get`` — a missing one becomes all-null
    # instead of a KeyError, and ``to_numeric`` never sees a None.
    for column in ("salary", "points"):
        if column not in frame.columns:
            frame[column] = pd.NA
    salary = pd.to_numeric(frame["salary"], errors="coerce")
    points = pd.to_numeric(frame["points"], errors="coerce")
    frame["salary"] = salary
    frame["points"] = points
    frame["value"] = (points / (salary / 1000.0)).where(salary > 0)
    taken = {
        (str(slot.player_name), str(slot.position))
        for slot in (run.lineup.slots if run.lineup is not None else [])
    }
    frame["rostered"] = [
        (str(name), str(position)) in taken
        for name, position in zip(frame["player_name"], frame["position"], strict=True)
    ]
    frame["draft_group_id"] = run.draft_group_id
    for column in POOL_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    return (frame[POOL_COLUMNS]
            .sort_values("points", ascending=False, kind="stable")
            .reset_index(drop=True))


def lineup_frame(run: LineupRun) -> pd.DataFrame:
    """The solved lineup as a persistable frame (empty when unsolved)."""
    if run.lineup is None:
        return pd.DataFrame(
            columns=["slot", "player_name", "position", "team", "salary",
                     "points", "kickoff"]
        )
    return pd.DataFrame([
        {"slot": s.slot, "player_name": s.player_name, "position": s.position,
         "team": s.team, "salary": s.salary, "points": s.points,
         "kickoff": s.kickoff}
        for s in run.lineup.slots
    ]).assign(draft_group_id=run.draft_group_id)


# What each contest type pays for. Cash games pay the median (you need to beat
# a line, not the field); a single-entry tournament pays a good-but-reachable
# game; a GPP is won in the tail. Pricing all four off the mean prices them as
# if they were one contest, which is how a cash lineup ends up full of
# boom-or-bust tournament plays.
CONTEST_OBJECTIVES: tuple[tuple[str, str], ...] = (
    ("cash", "median"),
    ("single_entry", "p75"),
    ("gpp", "p90"),
    ("ceiling", "p99"),
)


def contest_lineups(  # noqa: PLR0913 - one argument per part of a solve
    salaries: pd.DataFrame,
    fp: pd.DataFrame,
    *,
    slates: Sequence[SlateInfo],
    spec: RosterSpec,
    scorer: object = None,
    distributions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """The best lineup for each contest type, on each slate — as ROSTERS.

    The DFS Optimizer surface used to be the player pool with a column per
    contest quantile, which is the ingredients rather than the meal: it says
    what every player is worth in a GPP without ever saying which nine to
    play. This solves the roster the operator actually enters.

    One solve per (slate, contest): the objective is that contest's quantile
    from the banked per-sim arrays, so the cash roster maximises median points
    and the GPP roster maximises the 90th percentile, off the SAME draws. With
    no distribution frame the mean is the only objective available and a
    single ``projection`` contest is returned, which is what the surface
    showed before.
    """
    if distributions is None or distributions.empty or "player" not in distributions:
        objectives: list[tuple[str, pd.DataFrame | None]] = [("projection", None)]
    else:
        objectives = []
        for contest, column in CONTEST_OBJECTIVES:
            if column not in distributions.columns:
                continue
            quantile = distributions[["player", column]].rename(
                columns={"player": "player_name", column: "points"}
            )
            objectives.append((contest, quantile.dropna(subset=["points"])))
        if not objectives:
            objectives = [("projection", None)]

    rows: list[pd.DataFrame] = []
    for slate in slates:
        board = salaries[
            salaries["draft_group_id"].astype(str) == str(slate.draft_group_id)
        ]
        game_type = (str(board["game_type"].iloc[0])
                     if "game_type" in board.columns and not board.empty else "")
        for contest, points in objectives:
            run = solve_slate(salaries, fp, draft_group=slate.draft_group_id,
                              spec=spec, scorer=scorer, points=points)
            if run.lineup is None:
                continue
            frame = lineup_frame(run)
            if frame.empty:
                continue
            rows.append(frame.assign(
                contest=contest,
                slate=slate.suffix or str(slate.draft_group_id),
                game_type=game_type,
                n_games=slate.n_games,
                lineup_salary=run.lineup.total_salary,
                lineup_points=round(float(run.lineup.total_points), 2),
            ))
    if not rows:
        return pd.DataFrame(columns=[
            "contest", "slate", "game_type", "n_games", "slot", "player_name",
            "position", "team", "salary", "points", "kickoff",
            "lineup_salary", "lineup_points", "draft_group_id",
        ])
    return pd.concat(rows, ignore_index=True)
