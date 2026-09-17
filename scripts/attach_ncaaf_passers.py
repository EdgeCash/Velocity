"""Attach the passer's id to every college play, from cfbfastR's player stats.

CFBD's play-by-play (the committed ``datasets/ncaaf/plays.parquet``) names the
offense and the play type but not the passer. cfbfastR publishes a per-play
player-stats frame keyed by the same ESPN play id
(:data:`velocity.ingest.ncaaf.CFBFASTR_PLAYER_STATS_URL`; public, no key) —
the completion, incompletion, interception-thrown and sack-taken roles give
the passer on 88–97% of pass-type plays a season. This script joins it on as
``passer_player_id``, the column the QB decomposition
(``velocity.features.team.fit_qb_ratings``) keys on, season by season::

    python scripts/attach_ncaaf_passers.py --cache /path/to/cfb

``--cache`` holds the downloaded ``player_stats_{season}.parquet`` files
(missing seasons are fetched into it). The daily refresh runs the same join
for the current season (``scripts/refresh_datasets.py``).
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

import pandas as pd
from velocity.ingest.ncaaf import CFBFASTR_PLAYER_STATS_URL, attach_passers, passer_by_play


def fetch_player_stats(season: int, cache: Path) -> Path:  # pragma: no cover - network
    """The season's cfbfastR player-stats parquet on disk, downloaded if absent."""
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"player_stats_{season}.parquet"
    if not target.exists():
        url = CFBFASTR_PLAYER_STATS_URL.format(season=season)
        with urllib.request.urlopen(url, timeout=120) as resp, target.open("wb") as fh:  # noqa: S310
            fh.write(resp.read())
    return target


def coverage(plays: pd.DataFrame) -> pd.DataFrame:
    """Per season: pass-type plays and the share of them with a passer."""
    kind = plays["play_type"].astype("string").str.lower()
    is_pass = kind.str.contains("pass|sack|interception", regex=True).fillna(False)
    sub = plays[is_pass.to_numpy(dtype=bool)]
    return (sub.assign(has=sub["passer_player_id"].notna())
            .groupby("season")["has"].agg(pass_plays="size", with_passer="mean")
            .reset_index())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--plays", default="datasets/ncaaf/plays.parquet")
    parser.add_argument("--cache", required=True, help="folder for the cfbfastR parquets")
    parser.add_argument("--seasons", nargs="*", type=int, default=None,
                        help="seasons to attach (default: every season in the plays file)")
    args = parser.parse_args()

    plays = pd.read_parquet(args.plays)
    seasons = args.seasons or sorted(int(s) for s in plays["season"].unique())
    for season in seasons:
        try:
            path = fetch_player_stats(season, Path(args.cache))
        except Exception as exc:  # noqa: BLE001 - a missing season is reported, not fatal
            print(f"  {season}: no player stats ({exc})")
            continue
        passers = passer_by_play(pd.read_parquet(path))
        mask = plays["season"] == season
        plays.loc[mask, "passer_player_id"] = attach_passers(
            plays[mask], passers)["passer_player_id"].to_numpy()
        print(f"  {season}: {int(mask.sum())} plays, {len(passers)} passer rows")
    plays["passer_player_id"] = plays["passer_player_id"].astype("string")
    plays.to_parquet(args.plays, index=False)
    print(coverage(plays).to_string(index=False))


if __name__ == "__main__":
    main()
