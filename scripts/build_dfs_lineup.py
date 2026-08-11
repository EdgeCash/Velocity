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
    args = parser.parse_args()

    from velocity.dfs.optimizer import CFB_CLASSIC, NFL_CLASSIC
    from velocity.dfs.pipeline import lineup_frame, solve_slate
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
