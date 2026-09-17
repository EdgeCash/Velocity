"""Re-key synthetic NCAAF game ids onto CFBD's — one pass over the committed frame.

    python scripts/rekey_ncaaf_games.py --data datasets/ncaaf

The boxscore backfill (``backfill_games_from_boxscores.py``) mints ids of its
own for a season the CFBD pull had not covered; every other college frame
keys on CFBD's numeric id. ``attach_cfbd_lines.py`` now adopts the pulled id
when it attaches a line, so the mismatch cannot recur for a season that goes
through it — this script is the one-off for the rows already on file, using
the lines pull (``games_lines.parquet``) as the reference and the plays frame
as the fallback. Pure logic lives in
:func:`velocity.ingest.ncaaf.rekey_games_to_cfbd`; this only reads, applies
and writes.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.ingest.ncaaf import rekey_games_to_cfbd


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-key synthetic NCAAF game ids to CFBD's")
    parser.add_argument("--data", default="datasets/ncaaf")
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args()
    folder = Path(args.data)
    games = pd.read_parquet(folder / "games.parquet")
    reference = pd.read_parquet(folder / "games_lines.parquet")
    plays_file = folder / "plays.parquet"
    play_columns = ["game_id", "season", "week", "posteam", "defteam"]
    plays = (pd.read_parquet(plays_file, columns=play_columns)
             if plays_file.exists() else None)
    rekeyed, counts = rekey_games_to_cfbd(games, reference, plays)
    print(", ".join(f"{k} {v}" for k, v in counts.items()))
    if rekeyed["game_id"].duplicated().any():
        raise SystemExit("re-keying produced duplicate game ids; nothing written")
    if args.dry_run:
        return
    rekeyed.to_parquet(folder / "games.parquet", index=False)
    print(f"wrote {len(rekeyed)} games to {folder / 'games.parquet'}")


if __name__ == "__main__":
    main()
