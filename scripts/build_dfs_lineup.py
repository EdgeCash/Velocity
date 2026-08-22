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


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the optimal DK lineup")
    parser.add_argument("--salaries", required=True,
                        help="normalized DK salaries parquet")
    parser.add_argument("--fp", required=True,
                        help="FantasyPros projections parquet (long frame)")
    parser.add_argument("--out", required=True, help="output folder")
    parser.add_argument("--league", default="nfl", choices=["nfl", "ncaaf"])
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
    args = parser.parse_args()

    from velocity.dfs.optimizer import CFB_CLASSIC, NFL_CLASSIC
    from velocity.dfs.pipeline import is_season_long, lineup_frame, solve_slate
    from velocity.report.dfs_png import dfs_caption, render_dfs_card

    salaries = pd.read_parquet(args.salaries)
    if "league" in salaries.columns:
        salaries = salaries[salaries["league"] == args.league]
    fp = pd.read_parquet(args.fp)
    if "league" in fp.columns:
        fp = fp[fp["league"] == args.league]
    if salaries.empty or fp.empty:
        print("empty salaries or projections; no lineup to build")
        return
    if is_season_long(fp):
        print("FP snapshot carries season-long (week 0) projections — a weekly "
              "lineup can't be priced from season totals; skipping")
        return

    spec = NFL_CLASSIC if args.league == "nfl" else CFB_CLASSIC
    run = solve_slate(salaries, fp, draft_group=args.draft_group, spec=spec)
    if run.lineup is None:
        print(f"no solvable lineup (group {run.draft_group_id or 'none'}: "
              f"{run.n_salaried} salaried, {run.n_pool} projected)")
        return
    print(f"draft group {run.draft_group_id}: {run.n_games} games, "
          f"{run.n_pool}/{run.n_salaried} players projected")
    print(f"optimal lineup: ${run.lineup.total_salary:,} · "
          f"{run.lineup.total_points:.1f} DK pts")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    frame_dest = out / f"dfs_lineup_{args.league}_{stamp}.parquet"
    lineup_frame(run).to_parquet(frame_dest, index=False)
    print(f"wrote lineup frame to {frame_dest}")

    if args.gpp > 0:
        # Best-effort like every surface past the cash lineup.
        try:
            from velocity.dfs.gpp import GppConfig, build_gpp_portfolio, portfolio_frame
            from velocity.dfs.optimizer import lineup_pool
            from velocity.dfs.scoring import dk_expected_points
            from velocity.util.seed import make_rng

            board = salaries[
                salaries["draft_group_id"].astype(str) == str(run.draft_group_id)
            ]
            pool = lineup_pool(board, dk_expected_points(fp))
            portfolio = build_gpp_portfolio(
                pool, spec=spec, rng=make_rng(),
                config=GppConfig(n_lineups=args.gpp, max_overlap=args.gpp_overlap,
                                 max_exposure=args.gpp_exposure),
            )
            print(f"GPP portfolio: {len(portfolio.lineups)}/{args.gpp} lineups "
                  f"({portfolio.n_stacked} stacked of {portfolio.n_candidates} candidates)")
            if portfolio.lineups:
                gpp_dest = out / f"dfs_gpp_{args.league}_{stamp}.parquet"
                portfolio_frame(portfolio).to_parquet(gpp_dest, index=False)
                print(f"wrote GPP portfolio to {gpp_dest}")
        except Exception as exc:  # noqa: BLE001 - the portfolio never breaks the lineup
            print(f"GPP portfolio skipped: {exc}")

    kind = "DK CLASSIC" if args.league == "nfl" else "DK CFB CLASSIC"
    slate_label = f"{kind} · {run.n_games} GAMES"
    when = datetime.now(UTC).strftime("%A, %b %-d").upper()
    card_dest = out / f"dfs_{args.league}_{stamp}.png"
    render_dfs_card(run.lineup, card_dest, when=when, slate_label=slate_label)
    print(f"rendered lineup card to {card_dest}")
    captions = out / f"dfs_{args.league}_{stamp}_captions.md"
    captions.write_text(
        dfs_caption(run.lineup, slate_label=f"{kind.lower()} ({run.n_games} games)")
        + "\n"
    )


if __name__ == "__main__":
    main()
