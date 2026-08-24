"""Snapshot DraftKings salaries to a private archive (the DFS input).

For each requested league this pulls the DK lobby (draft groups = slates),
then each group's draftables (players + salaries), and:

* banks the **raw** lobby + per-group JSON verbatim (nothing DK served is
  lost, even fields without a normalizer yet), and
* writes one normalized :class:`~velocity.dfs.salaries.Salaries` parquet per
  league covering every draft group on the board.

Runs as a GitHub Action and uploads a PRIVATE artifact — salary history is
part of the edge, so it stays out of the public repo like the odds archives
(docs/FOOTBALL_CUTOVER.md §5a). An empty lobby (off-season) succeeds.

    python scripts/collect_dk_salaries.py --out artifacts/dk_salaries

    # offline re-processing of a banked draftables payload (also the test path):
    python scripts/collect_dk_salaries.py --from-file draftables.json \
        --draft-group 12345 --league nfl --out artifacts/dk_salaries
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.dfs.salaries import (
    SPORT_CODES,
    normalize_draft_groups,
    normalize_draftables,
)


def _write_league(
    league: str,
    frames: list[pd.DataFrame],
    out: Path,
    tag: str,
    collected_at: pd.Timestamp,
    groups: pd.DataFrame | None = None,
) -> None:
    salaries = (
        pd.concat(frames, ignore_index=True) if frames else normalize_draftables({}, "0")
    ).assign(league=league, collected_at=collected_at)
    if groups is not None and not groups.empty and not salaries.empty:
        # Slate identity rides with every row: the game style (classic vs
        # showdown/tiers/...), the slate's start, and DK's own grouping label
        # ("Turbo"/"Night"/"Early") — what the multi-slate solver keys on.
        meta = groups.rename(columns={"start": "slate_start"})[
            ["draft_group_id", "contest_type_id", "slate_start", "suffix"]
        ]
        salaries = salaries.merge(meta, on="draft_group_id", how="left")
    dest = out / f"dk_salaries_{league}_{tag}.parquet"
    salaries.to_parquet(dest, index=False)
    n_groups = salaries["draft_group_id"].nunique() if not salaries.empty else 0
    print(f"  {league}: {len(salaries)} salary rows across {n_groups} draft group(s) → {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot DraftKings salaries")
    parser.add_argument("--out", default="artifacts/dk_salaries",
                        help="output folder (private)")
    parser.add_argument("--leagues", default="nfl ncaaf mlb wnba nba ncaab nhl",
                        help="space-separated leagues to snapshot")
    parser.add_argument("--from-file",
                        help="saved draftables payload JSON (offline; single group)")
    parser.add_argument("--draft-group", default="0",
                        help="draft group id label for --from-file")
    parser.add_argument("--league", default="nfl", help="league label for --from-file")
    args = parser.parse_args()

    now = datetime.now(UTC)
    collected_at = pd.Timestamp(now).tz_localize(None)
    tag = now.strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out)
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    if args.from_file:
        payload = json.loads(Path(args.from_file).read_text())
        frame = normalize_draftables(payload, args.draft_group)
        _write_league(args.league, [frame], out, tag, collected_at)
        print("processed 1 draft group from file")
        return

    from velocity.dfs.salaries import DraftKingsClient  # network path

    client = DraftKingsClient()
    print(f"DK salary snapshot @ {now.isoformat()}")
    for league in args.leagues.split():
        sport = SPORT_CODES.get(league)
        if sport is None:
            print(f"  {league}: no DK sport code known; skipping")
            continue
        try:
            lobby = client.lobby(sport)
        except Exception as exc:  # noqa: BLE001 - one league's lobby never blocks the other
            print(f"  {league}: lobby fetch failed ({exc}); skipping")
            continue
        (raw_dir / f"{league}_lobby_{tag}.json").write_text(json.dumps(lobby))
        groups = normalize_draft_groups(lobby)
        print(f"  {league}: {len(groups)} draft group(s) in the lobby")

        frames: list[pd.DataFrame] = []
        for group_id in groups["draft_group_id"]:
            try:
                payload = client.draftables(group_id)
            except Exception as exc:  # noqa: BLE001 - one group's fetch never blocks the rest
                print(f"  {league}: draft group {group_id} skipped ({exc})")
                continue
            (raw_dir / f"{league}_draftables_{tag}_{group_id}.json").write_text(
                json.dumps(payload)
            )
            frames.append(normalize_draftables(payload, str(group_id)))
        _write_league(league, frames, out, tag, collected_at, groups=groups)


if __name__ == "__main__":
    main()
