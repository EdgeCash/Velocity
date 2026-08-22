"""Build the NCAAB datasets: games, team boxes, and Torvik season ratings.

Phase N2's data (docs/BUILD_NCAAB.md): hoopR schedules (completed games with
finals and neutral flags) and team boxes (possession components) feed the
pace×efficiency fit; Torvik season-end ratings feed the early-season prior —
season N's prior is season N−1's final ratings, which is leak-free by
construction.

    python scripts/build_ncaab_datasets.py --start 2019 --end 2026 --out datasets/ncaab

Free public data, slim frames, commit-friendly sizes.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from velocity.ingest.ncaab import TorvikClient, load_hoopr_schedule, load_hoopr_team_box


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank NCAAB games/boxes/ratings")
    parser.add_argument("--start", type=int, default=2019)
    parser.add_argument("--end", type=int, default=2026)
    parser.add_argument("--out", default="datasets/ncaab")
    parser.add_argument("--skip-torvik", action="store_true",
                        help="games + boxes only (Torvik asks bulk scrapers to make contact)")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    seasons = list(range(args.start, args.end + 1))

    games_frames, box_frames, torvik_frames = [], [], []
    client = TorvikClient()
    for season in seasons:
        try:
            games = load_hoopr_schedule(season)
            games_frames.append(games)
            print(f"{season}: {len(games)} completed games")
        except Exception as exc:  # noqa: BLE001 - a missing season is a gap, not a failure
            print(f"{season}: schedule skipped ({exc})")
        try:
            box = load_hoopr_team_box(season)
            box_frames.append(box)
            print(f"{season}: {len(box)} team-box rows")
        except Exception as exc:  # noqa: BLE001
            print(f"{season}: team box skipped ({exc})")
        if not args.skip_torvik:
            try:
                ratings = client.team_results(season)
                torvik_frames.append(ratings)
                print(f"{season}: {len(ratings)} Torvik team ratings")
            except Exception as exc:  # noqa: BLE001
                print(f"{season}: torvik skipped ({exc})")

    if games_frames:
        games = pd.concat(games_frames, ignore_index=True)
        games.to_parquet(out / "games.parquet", index=False)
        print(f"banked {len(games)} games → {out / 'games.parquet'}")
    if box_frames:
        box = pd.concat(box_frames, ignore_index=True)
        box.to_parquet(out / "team_box.parquet", index=False)
        print(f"banked {len(box)} box rows → {out / 'team_box.parquet'}")
    if torvik_frames:
        torvik = pd.concat(torvik_frames, ignore_index=True)
        torvik.to_parquet(out / "torvik.parquet", index=False)
        print(f"banked {len(torvik)} ratings rows → {out / 'torvik.parquet'}")


if __name__ == "__main__":
    main()
