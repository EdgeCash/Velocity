"""Build the college player-game dataset from the cfbfastR release parquets.

NCAAF was the only in-season sport with a DraftKings roster spec, daily salary
collection, and no player data of any kind to price it with: the plays frame
carries eleven columns with no player fields and the box scores are game
level. CFBD serves player statistics behind an API key; cfbfastR publishes the
same substrate keyless on the raw-CDN transport the WNBA and NCAAB verticals
already use, so this needs no secret and works from CI.

The release is play-level; :func:`velocity.ingest.cfb_players.player_games`
folds it to one row per player-game in the NFL DFS vocabulary, which is also
the right scoring line — DK's college scoring is its NFL scoring.

**Coverage is the thing to watch.** The release fills progressively, and a
season it has not finished attributing looks like a season where nobody
scored. The run prints the share of team-games whose final score its own
touchdowns and field goals explain, per season; below about half, that season
cannot price a lineup and says so.

    python scripts/build_cfb_player_games.py --seasons 2023 2024 2025 2026
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

# Below this share of team-games explained, a season cannot price a lineup.
MIN_COVERAGE = 0.5


def bank_player_games(
    seasons: list[int], out_path: str | Path
) -> int:  # pragma: no cover - network orchestration
    """Refresh ``seasons`` in the committed frame, keep other seasons as-is."""
    from velocity.ingest.cfb_players import fetch_player_games

    out_path = Path(out_path)
    existing = pd.read_parquet(out_path) if out_path.exists() else pd.DataFrame()
    frames = []
    for season in seasons:
        try:
            frame, covered = fetch_player_games(season)
        except Exception as exc:  # noqa: BLE001 - a missing season never blocks
            print(f"  {season}: unavailable ({exc})")
            continue
        note = "usable" if covered >= MIN_COVERAGE else "TOO THIN to price a lineup"
        print(f"  {season}: {len(frame)} player-games, "
              f"{frame['game_id'].nunique()} games, coverage {covered:.1%} — {note}")
        frames.append(frame)
    if not frames:
        print("nothing fetched; leaving the dataset as it was")
        return 0
    fresh = pd.concat(frames, ignore_index=True)
    if not existing.empty:
        keep = existing[~existing["season"].isin(fresh["season"].unique())]
        fresh = pd.concat([keep, fresh], ignore_index=True)
    fresh = fresh.sort_values(["season", "week", "game_id", "player_id"])
    fresh = fresh.reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fresh.to_parquet(out_path, index=False)
    print(f"wrote {len(fresh)} player-games "
          f"({fresh['player_id'].nunique()} players) to {out_path}")
    return len(fresh)


def main() -> None:  # pragma: no cover - network orchestration
    parser = argparse.ArgumentParser(description="Bank college player-games")
    parser.add_argument("--seasons", type=int, nargs="+",
                        default=[2023, 2024, 2025, 2026])
    parser.add_argument("--out", default="datasets/ncaaf/player_games.parquet")
    args = parser.parse_args()
    bank_player_games(args.seasons, args.out)


if __name__ == "__main__":
    main()
