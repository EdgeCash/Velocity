"""Build the optimal DK classic lineup from the day's snapshots → card + frame.

Inputs are the two private artifacts the daily loop already collects: the DK
salary snapshot (``collect_dk_salaries.py``) and the FantasyPros projections
snapshot (``collect_fantasypros.py``). The main slate is auto-picked (most
games on the board); ``--draft-group`` pins a specific one. Outputs land in
``--out``: the lineup parquet (private-artifact material), the lineup card
PNG, and a captions file of post copy.

Every failure mode (no salaries, empty board, infeasible pool) exits 0 with a
message — the DFS surface is additive and never blocks the slate.

    python scripts/build_dfs_lineup.py \
        --salaries artifacts/dk_salaries/dk_salaries_nfl_<stamp>.parquet \
        --fp artifacts/fantasypros/fp_nfl_<stamp>.parquet \
        --out artifacts/slate
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


def apply_confirmed_cards(
    slot_of: dict[str, int], team_of: dict[str, str], start: str, end: str,
) -> set[str] | None:
    """Fold statsapi's posted batting orders into the slot and team maps.

    Mutates ``slot_of`` (the confirmed order beats a guess from his last
    start) and ``team_of`` (tonight's club beats the one he last played for,
    which matters for a call-up), and returns the set of batter ids still
    choosable: the announced nine for every team whose card is posted, plus
    everyone on a team whose card is not.

    Returns ``None`` when nothing is posted — the pool is unrestricted, and
    the caller prices every banked bat exactly as before. This is the single
    most valuable input the MLB DFS surfaces take: an early build carries
    2.1 players who never appear out of six (docs/DFS_FORMATS.md).
    """
    from build_mlb_pitching import fetch_lineups

    try:
        cards = fetch_lineups(start, end)
    except Exception as exc:  # noqa: BLE001 - falls back to recent slots
        print(f"confirmed lineups unavailable ({exc}); using recent slots")
        return None
    if not cards:
        print("no confirmed lineups posted yet; using recent slots")
        return None
    confirmed_teams = set(cards)
    for team, order in cards.items():
        for i, pid in enumerate(order):
            slot_of[str(pid)] = i + 1
            team_of[str(pid)] = team
    eligible = {pid for order in cards.values() for pid in order}
    eligible |= {pid for pid, team in team_of.items()
                 if team not in confirmed_teams}
    print(f"confirmed lineups: {len(confirmed_teams)} teams, "
          f"{len(eligible)} eligible bats")
    return eligible


def _contextual_mlb_points() -> pd.DataFrame:
    """Today's contextual MLB DK projections from the committed banks.

    Opposing starters come from the free statsapi probables feed; the park is
    each game's home club. The lineup slot is the batter's **confirmed** slot
    when statsapi has posted the card, and his most recent starting slot
    otherwise.

    The confirmed card is the single most valuable input here, ahead of every
    context multiplier. Measured on the showdown backtest
    (docs/DFS_FORMATS.md): a roster built before lineups post carries 2.1
    players who never appear out of six, and the same optimizer fed the
    confirmed card scores about 11 DK points more. So a team with a posted
    lineup contributes exactly those nine bats and nobody else.
    """
    from datetime import date, timedelta

    from build_mlb_pitching import fetch_probables
    from velocity.dfs.scoring import dk_expected_points_mlb_contextual

    batters = pd.read_parquet("datasets/mlb/batters.parquet")
    starters = pd.read_parquet("datasets/mlb/starters.parquet")
    games = pd.read_parquet("datasets/mlb/games.parquet")
    season = int(games["season"].max())

    ids = set(games.loc[games["season"] == season, "game_id"].astype(str))
    recent = batters[batters["game_id"].astype(str).isin(ids)]
    if "started" in recent.columns:
        recent = recent[recent["started"].astype(bool)]
    recent = recent.sort_values("game_id").drop_duplicates("batter_id", keep="last")
    slot_of = {str(r["batter_id"]): int(r["lineup_slot"])
               for r in recent.to_dict("records")}
    team_of = {str(r["batter_id"]): str(r["team"])
               for r in recent.to_dict("records")}

    today = date.today()
    tomorrow = today + timedelta(days=1)

    eligible = apply_confirmed_cards(slot_of, team_of, str(today), str(tomorrow))

    probables = fetch_probables(str(today), str(tomorrow))
    venue_of_team: dict[str, str] = {}
    facing: dict[str, str] = {}
    for (home, away, _k), (home_sp, away_sp) in probables.items():
        venue_of_team[home] = home
        venue_of_team[away] = home  # the away club plays in the home park
        for pid, team in team_of.items():
            if team == home and away_sp:
                facing[pid] = str(away_sp)
            elif team == away and home_sp:
                facing[pid] = str(home_sp)

    return dk_expected_points_mlb_contextual(
        batters, starters, games, opposing_starter=facing,
        venue_of_team=venue_of_team, lineup_slot=slot_of,
        eligible_batters=eligible, season=season)


def build_showdown_boards(
    salaries: pd.DataFrame,
    fp: pd.DataFrame,
    points: pd.DataFrame | None,
    league: str,
    out: Path,
    stamp: str,
) -> None:
    """Solve every Showdown Captain Mode board on the snapshot (best-effort).

    Writes one parquet carrying all solved boards, then a card + captions for
    the strongest board (highest projected total) — the one worth posting.
    Like every surface past the cash lineup this never raises: a showdown
    failure must not cost the slate its classic lineup.
    """
    from velocity.dfs.pipeline import (
        game_time_ct,
        lineup_frame,
        showdown_slates,
        solve_showdown,
    )
    from velocity.dfs.showdown import SHOWDOWN_SPECS
    from velocity.report.dfs_png import dfs_caption, render_dfs_card

    if league not in SHOWDOWN_SPECS:
        return
    boards = showdown_slates(salaries, league)
    if not boards:
        print("no showdown boards on the snapshot")
        return
    frames: list[pd.DataFrame] = []
    solved: list[tuple] = []
    for board in boards:
        run = solve_showdown(salaries, fp, draft_group=board.draft_group_id,
                             league=league, points=points)
        if run.lineup is None:
            print(f"showdown {board.suffix or board.draft_group_id}: no lineup "
                  f"({run.n_salaried} salaried, {run.n_pool} projected)")
            continue
        print(f"showdown {board.suffix} (group {run.draft_group_id}): "
              f"{run.n_pool}/{run.n_salaried} projected → "
              f"${run.lineup.total_salary:,} · {run.lineup.total_points:.1f} DK pts")
        rows = lineup_frame(run)
        frames.append(rows.assign(slate_start=board.start, suffix=board.suffix,
                                  slate=board.suffix, format="showdown",
                                  game_time=rows["kickoff"].map(game_time_ct)))
        solved.append((board, run))
    if not solved:
        return
    dest = out / f"dfs_showdown_{league}_{stamp}.parquet"
    pd.concat(frames, ignore_index=True).to_parquet(dest, index=False)
    print(f"wrote {len(frames)} showdown lineup(s) to {dest}")

    board, run = max(solved, key=lambda pair: pair[1].lineup.total_points)
    kind = {"nfl": "DK NFL SHOWDOWN", "ncaaf": "DK CFB SHOWDOWN",
            "mlb": "DK MLB SHOWDOWN"}[league]
    label = f"{kind} · {board.suffix}" if board.suffix else kind
    lock = game_time_ct(board.start)
    if lock != "\u2014":
        label = f"{label} · {lock}"
    source = ("statsapi season rates scored as DK points" if league == "mlb"
              else "FantasyPros consensus scored as DK points")
    when = datetime.now(UTC).strftime("%A, %b %-d").upper()
    card = out / f"dfs_showdown_{league}_{stamp}.png"
    render_dfs_card(run.lineup, card, when=when, slate_label=label.upper(),
                    source_note=source)
    print(f"rendered showdown card to {card}")
    captions = out / f"dfs_showdown_{league}_{stamp}_captions.md"
    captions.write_text(
        dfs_caption(run.lineup,
                    slate_label=f"{kind.lower()} ({board.suffix})") + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the optimal DK lineup")
    parser.add_argument("--salaries", required=True,
                        help="normalized DK salaries parquet")
    parser.add_argument("--fp", required=True,
                        help="FantasyPros projections parquet (long frame)")
    parser.add_argument("--out", required=True, help="output folder")
    parser.add_argument("--league", default="nfl",
                        help="league to price (needs a roster spec — "
                             "velocity.dfs.pipeline.LEAGUE_SPECS; others skip)")
    parser.add_argument("--draft-group", default=None,
                        help="pin a draft group id (default: auto-pick main slate)")
    # GPP portfolio (docs/EDGE_RESEARCH.md §5): tournaments pay the tail of a
    # huge field — the documented play is many diversified, stacked lineups
    # under overlap/exposure caps, not the single cash-optimal build.
    parser.add_argument("--gpp", type=int, default=0,
                        help="also build a GPP portfolio of this many lineups (0 = off)")
    parser.add_argument("--gpp-overlap", type=int, default=5,
                        help="max players any two GPP lineups may share")
    parser.add_argument("--gpp-exposure", type=float, default=0.6,
                        help="max fraction of GPP lineups any player appears in")
    parser.add_argument("--gpp-stack", type=int, default=4,
                        help="MLB GPP: hitters required from one batting order "
                             "(DK caps a classic roster at 5; 0 = off)")
    parser.add_argument("--gpp-secondary", type=int, default=2,
                        help="MLB GPP: hitters required from a second club "
                             "(the mini-stack; 0 = off)")
    parser.add_argument("--no-showdown", dest="showdown", action="store_false",
                        help="skip the single-game Showdown Captain Mode boards")
    args = parser.parse_args()

    from velocity.dfs.pipeline import (
        LEAGUE_SPECS,
        is_season_long,
        lineup_frame,
        solve_slate,
    )
    from velocity.report.dfs_png import dfs_caption, render_dfs_card

    if args.league not in LEAGUE_SPECS:
        print(f"{args.league}: no DK roster spec/scorer yet; skipping")
        return
    spec, scorer = LEAGUE_SPECS[args.league]
    salaries = pd.read_parquet(args.salaries)
    if "league" in salaries.columns:
        salaries = salaries[salaries["league"] == args.league]
    fp = pd.read_parquet(args.fp)
    if "league" in fp.columns:
        fp = fp[fp["league"] == args.league]
    if salaries.empty or fp.empty:
        print("empty salaries or projections; no lineup to build")
        return
    # Football projections must be a real week — season totals price nonsense.
    # The MLB scorer normalizes season totals to per-game rates itself, so
    # week-0 frames are exactly what it expects.
    if args.league in ("nfl", "ncaaf") and is_season_long(fp):
        print("FP snapshot carries season-long (week 0) projections — a weekly "
              "lineup can't be priced from season totals; skipping")
        return

    # Every classic slate grouping DK posted (main + Early/Night/Turbo), each
    # solved on its own board; --draft-group pins one. The card renders the
    # main slate; the parquet carries them all for the site's DFS page.
    from velocity.dfs.pipeline import (
        SlateInfo,
        classic_slates,
        game_time_ct,
        slate_label_ct,
    )

    # MLB prices from the CONTEXTUAL model (docs/DFS_MODEL.md): the banked
    # box scores plus today's probables, park and lineup slot. Best-effort —
    # if a bank or the probables feed is missing, the flat scorer still runs.
    points = None
    if args.league == "mlb":
        try:
            points = _contextual_mlb_points()
            print(f"contextual MLB projections: {len(points)} players")
        except Exception as exc:  # noqa: BLE001 - falls back to the flat scorer
            print(f"contextual projections unavailable ({exc}); using flat rates")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if args.showdown:
        # Single-game captain-mode boards, solved before the classic slates so
        # a showdown-only day (an NFL standalone, a light MLB card) still
        # produces a lineup.
        build_showdown_boards(salaries, fp, points, args.league, out, stamp)

    if args.draft_group:
        slates = [SlateInfo(str(args.draft_group), None, "", 0)]
    else:
        slates = classic_slates(salaries)
    if not slates:
        print("no multi-game slate groupings on the board")
        return

    frames: list[pd.DataFrame] = []
    solved: list[tuple] = []
    for slate in slates:
        run = solve_slate(salaries, fp, draft_group=slate.draft_group_id,
                          spec=spec, scorer=scorer, points=points)
        label = slate_label_ct(slate)
        if run.lineup is None:
            print(f"{label or slate.draft_group_id}: no solvable lineup "
                  f"({run.n_salaried} salaried, {run.n_pool} projected)")
            continue
        print(f"{label or slate.draft_group_id} (group {run.draft_group_id}): "
              f"{run.n_games} games, {run.n_pool}/{run.n_salaried} projected → "
              f"${run.lineup.total_salary:,} · {run.lineup.total_points:.1f} DK pts")
        rows = lineup_frame(run)
        frames.append(rows.assign(
            slate_start=slate.start, suffix=slate.suffix, slate=label,
            game_time=rows["kickoff"].map(game_time_ct)))
        solved.append((slate, run))
    if not solved:
        print("no solvable lineup on any slate grouping")
        return
    # Today's main slate fronts the card + GPP: among the slates locking on
    # the earliest date, the one with the most games (a bigger slate tomorrow
    # must not outrank tonight's board).
    first_day = min((s.start for s, _r in solved if s.start is not None),
                    default=None)
    todays = [pair for pair in solved
              if first_day is None or pair[0].start is None
              or pair[0].start.date() == first_day.date()]
    slate, run = max(todays or solved, key=lambda pair: pair[1].n_games)

    frame_dest = out / f"dfs_lineup_{args.league}_{stamp}.parquet"
    pd.concat(frames, ignore_index=True).to_parquet(frame_dest, index=False)
    print(f"wrote {len(frames)} slate lineup(s) to {frame_dest}")

    if args.gpp > 0:
        # Best-effort like every surface past the cash lineup. Football stacks
        # on a QB; baseball stacks on a batting order (velocity/dfs/gpp.py).
        try:
            from velocity.dfs.gpp import GppConfig, build_gpp_portfolio, portfolio_frame
            from velocity.dfs.optimizer import lineup_pool
            from velocity.dfs.pipeline import eligible_board, normalize_positions
            from velocity.dfs.scoring import dk_expected_points
            from velocity.util.seed import make_rng

            board = salaries[
                salaries["draft_group_id"].astype(str) == str(run.draft_group_id)
            ]
            board = eligible_board(normalize_positions(board, spec), spec)
            pool = lineup_pool(board, points if points is not None
                               else dk_expected_points(fp))
            portfolio = build_gpp_portfolio(
                pool, spec=spec, rng=make_rng(),
                config=GppConfig(n_lineups=args.gpp, max_overlap=args.gpp_overlap,
                                 max_exposure=args.gpp_exposure,
                                 mlb_stack=args.gpp_stack,
                                 mlb_secondary=args.gpp_secondary),
            )
            print(f"GPP portfolio: {len(portfolio.lineups)}/{args.gpp} lineups "
                  f"({portfolio.n_stacked} stacked of {portfolio.n_candidates} candidates)")
            if portfolio.lineups:
                gpp_dest = out / f"dfs_gpp_{args.league}_{stamp}.parquet"
                portfolio_frame(portfolio).to_parquet(gpp_dest, index=False)
                print(f"wrote GPP portfolio to {gpp_dest}")
        except Exception as exc:  # noqa: BLE001 - the portfolio never breaks the lineup
            print(f"GPP portfolio skipped: {exc}")

    import dataclasses

    kind = {"nfl": "DK CLASSIC", "ncaaf": "DK CFB CLASSIC",
            "mlb": "DK MLB CLASSIC"}.get(args.league, "DK CLASSIC")
    label = f"{kind} · {run.n_games} GAMES"
    # Lock time + grouping only — the game count is already stated.
    lock = slate_label_ct(dataclasses.replace(slate, n_games=0))
    if lock:
        label = f"{label} · {lock.upper()}"
    source = ("statsapi season rates scored as DK points" if args.league == "mlb"
              else "FantasyPros consensus scored as DK points")
    when = datetime.now(UTC).strftime("%A, %b %-d").upper()
    card_dest = out / f"dfs_{args.league}_{stamp}.png"
    render_dfs_card(run.lineup, card_dest, when=when, slate_label=label,
                    source_note=source)
    print(f"rendered lineup card to {card_dest}")
    captions = out / f"dfs_{args.league}_{stamp}_captions.md"
    caption_label = f"{kind.lower()} ({run.n_games} games"
    caption_label += f", {lock})" if lock else ")"
    captions.write_text(
        dfs_caption(run.lineup, slate_label=caption_label) + "\n"
    )


if __name__ == "__main__":
    main()
