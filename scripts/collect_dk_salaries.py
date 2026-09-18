"""Snapshot DraftKings salaries to a private archive (the DFS input).

For each requested league this pulls the DK lobby (draft groups = slates),
then each group's draftables (players + salaries), and:

* banks the **raw** lobby + per-group JSON verbatim (nothing DK served is
  lost, even fields without a normalizer yet), and
* writes one normalized :class:`~velocity.dfs.salaries.Salaries` parquet per
  league covering every draft group on the board.

Runs as a GitHub Action and uploads an Actions artifact — salary history is
part of the edge, so it stays out of git like the odds archives
(docs/FOOTBALL_CUTOVER.md §5a). An empty lobby (off-season) succeeds. A lobby
with boards that all refuse to be fetched does NOT pass quietly: each such
league gets a warning annotation, and ``--fail-on-empty`` (the collector
workflow's setting) fails the run when every league with boards came back
empty — from 2026-09-16 to 09-17 the draftables API 403'd every request and
the archive banked zero rows for two days behind green runs.

    python scripts/collect_dk_salaries.py --out artifacts/dk_salaries

    # offline re-processing of a banked draftables payload (also the test path):
    python scripts/collect_dk_salaries.py --from-file draftables.json \
        --draft-group 12345 --league nfl --out artifacts/dk_salaries
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.dfs.salaries import (
    SPORT_CODES,
    assign_draft_groups,
    normalize_draft_groups,
    normalize_draftables,
)
from velocity.dfs.tiered import normalize_tiered


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
            ["draft_group_id", "contest_type_id", "game_type", "slate_start",
             "suffix"]
        ]
        salaries = salaries.merge(meta, on="draft_group_id", how="left")
    dest = out / f"dk_salaries_{league}_{tag}.parquet"
    salaries.to_parquet(dest, index=False)
    n_groups = salaries["draft_group_id"].nunique() if not salaries.empty else 0
    print(f"  {league}: {len(salaries)} salary rows across {n_groups} draft group(s) → {dest}")


def _write_tiered(
    league: str,
    frames: list[pd.DataFrame],
    out: Path,
    tag: str,
    collected_at: pd.Timestamp,
    groups: pd.DataFrame | None = None,
) -> None:
    """Bank the SALARY-FREE boards (Tiers, Single Stat) as their own artifact.

    They cannot ride the salary parquet: every draftable on them ships
    ``salary: null``, and the salary normalizer drops a priceless row rather
    than invent a number. What stands in for the price is the tier ordinal
    (velocity.dfs.tiered), so these boards get their own file and their own
    schema.
    """
    if not frames:
        return
    tiered = pd.concat(frames, ignore_index=True).assign(
        league=league, collected_at=collected_at)
    if groups is not None and not groups.empty:
        meta = groups.rename(columns={"start": "slate_start"})[
            ["draft_group_id", "contest_type_id", "game_type", "slate_start",
             "suffix"]
        ]
        tiered = tiered.merge(meta, on="draft_group_id", how="left")
    dest = out / f"dk_tiered_{league}_{tag}.parquet"
    tiered.to_parquet(dest, index=False)
    n_groups = tiered["draft_group_id"].nunique()
    print(f"  {league}: {len(tiered)} tiered rows across {n_groups} "
          f"salary-free board(s) → {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot DraftKings salaries")
    parser.add_argument("--out", default="artifacts/dk_salaries",
                        help="output folder (artifact, never git)")
    parser.add_argument("--leagues", default="nfl ncaaf mlb wnba nba ncaab nhl",
                        help="space-separated leagues to snapshot")
    parser.add_argument("--from-file",
                        help="saved draftables payload JSON (offline; single group)")
    parser.add_argument("--draft-group", default="0",
                        help="draft group id label for --from-file")
    parser.add_argument("--league", default="nfl", help="league label for --from-file")
    parser.add_argument("--fail-on-empty", action="store_true",
                        help="exit non-zero when every league with boards in its "
                             "lobby fetched no salary rows (a refusal, not an "
                             "off-season)")
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

    # Every lobby first, then ownership, then the draftables. The lobby does
    # not reliably honour its sport filter, so a league's own boards can only
    # be told apart by which lobby claims them most specifically
    # (:func:`assign_draft_groups`). Resolving before fetching also stops the
    # collector pulling — and banking — another sport's whole board.
    lobbies: dict[str, pd.DataFrame] = {}
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
        lobbies[league] = normalize_draft_groups(lobby)

    owned = assign_draft_groups(
        {league: frame["draft_group_id"].tolist() for league, frame in lobbies.items()}
    )
    boards_seen = 0
    empty_leagues: list[str] = []
    for league, groups in lobbies.items():
        mine = owned.get(league, set())
        borrowed = len(groups) - len(mine)
        groups = groups[groups["draft_group_id"].isin(mine)].reset_index(drop=True)
        note = f" ({borrowed} belonged to another sport)" if borrowed else ""
        print(f"  {league}: {len(groups)} draft group(s) in the lobby{note}")

        frames: list[pd.DataFrame] = []
        tiered: list[pd.DataFrame] = []
        served: Counter[str] = Counter()
        last_error = ""
        for group in groups.itertuples(index=False):
            group_id = group.draft_group_id
            start = None if pd.isna(group.start) else pd.Timestamp(group.start).isoformat()
            try:
                payload = client.draftables(group_id, start=start)
            except Exception as exc:  # noqa: BLE001 - one group's fetch never blocks the rest
                print(f"  {league}: draft group {group_id} skipped ({exc})")
                last_error = str(exc)
                continue
            served[str(payload.get("_source", "api"))] += 1
            (raw_dir / f"{league}_draftables_{tag}_{group_id}.json").write_text(
                json.dumps(payload)
            )
            priced = normalize_draftables(payload, str(group_id))
            if priced.empty and (payload.get("draftables") or []):
                # A board DK served but priced nowhere: a salary-free format.
                tiered.append(normalize_tiered(payload, str(group_id)))
            else:
                frames.append(priced)
        if served.get("legacy"):
            print(f"  {league}: {served['legacy']} of {sum(served.values())} draft "
                  "group(s) served by the legacy lineup endpoint (the draftables "
                  "API refused them)")
        if len(groups) and not sum(served.values()):
            # Boards in the lobby, none fetched: a refusal, and the archive
            # is about to bank an empty day. Say so where the run summary
            # shows it rather than only in a green log.
            print(f"::warning title=DraftKings salaries empty::{league}: "
                  f"{len(groups)} draft group(s) in the lobby, none fetched "
                  f"(last error: {last_error})")
            empty_leagues.append(league)
        boards_seen += int(len(groups) > 0)
        _write_league(league, frames, out, tag, collected_at, groups=groups)
        _write_tiered(league, tiered, out, tag, collected_at, groups=groups)

    if args.fail_on_empty and boards_seen and len(empty_leagues) == boards_seen:
        print("::error title=DraftKings salaries empty::every lobby with boards "
              f"fetched nothing ({', '.join(empty_leagues)}); the archive banked "
              "no salaries this run")
        sys.exit(1)


if __name__ == "__main__":
    main()
