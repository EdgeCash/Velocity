"""Build DK's salary-free entries — Tiers and Single Stat — for a slate.

The formats with no cap and no optimizer worth the name: DK hands you a
pool (or six tiers of one) and your projection does the rest. That makes
them the purest test of the models Velocity already has — the contextual MLB
DK-points model for Tiers, the home-run model for Single Stat - Home Runs,
and the football consensus for Single Stat - Touchdowns.

Input is the tiered artifact the salary collector banks alongside the priced
boards (``dk_tiered_{league}_{stamp}.parquet``). Every failure mode exits 0
with a message — like every DFS surface, this is additive and never blocks
the slate.

    python scripts/build_dfs_tiered.py --league mlb \\
        --tiered artifacts/dk_salaries/dk_tiered_mlb_<stamp>.parquet \\
        --out artifacts/slate
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

# DK's game-type name → the spec that scores it. Snake formats are in the
# tiered artifact too (they are salary-free) but are drafted, not picked, so
# they are deliberately absent here.
_SUPPORTED = {
    "Tiers": "mlb_tiers",
    "Single Stat - Home Runs": "mlb_single_stat_hr",
    "Single Stat - Touchdowns": "cfb_single_stat_td",
}


def _mlb_dk_points() -> pd.DataFrame:
    """Today's contextual MLB DK projections (the same input Tiers wants)."""
    from build_dfs_lineup import _contextual_mlb_points

    return _contextual_mlb_points()


def _mlb_home_runs(season: int | None = None) -> pd.DataFrame:
    """Expected home runs per bat, from the validated HR model.

    Same construction as ``scripts/build_hr_board.py``: today's probables
    give each side's opposing starter, the venue is the home club, and the
    lineup slot is the batter's **confirmed** slot when statsapi has posted
    the card (his most recent start otherwise). A team with a posted card
    contributes exactly its announced nine — a bat that never leaves the
    bench hits no home runs.
    """
    from datetime import date, timedelta

    from build_dfs_lineup import apply_confirmed_cards
    from build_mlb_pitching import fetch_probables
    from velocity.models.props_hr import HomeRunModel

    batters = pd.read_parquet("datasets/mlb/batters.parquet")
    games = pd.read_parquet("datasets/mlb/games.parquet")
    starters = pd.read_parquet("datasets/mlb/starters.parquet")
    season = season or int(games["season"].max())
    model = HomeRunModel.fit(batters, games, starters, None, season=season)
    if not model.batter_rate:
        return pd.DataFrame(columns=["player_name", "team", "points"])

    ids = set(games.loc[games["season"] == season, "game_id"].astype(str))
    recent = batters[batters["game_id"].astype(str).isin(ids)]
    if "started" in recent.columns:
        recent = recent[recent["started"].astype(bool)]
    recent = recent.sort_values("game_id").drop_duplicates("batter_id", keep="last")
    slot_of = {str(r["batter_id"]): int(r["lineup_slot"])
               for r in recent.to_dict("records")}
    team_of = {str(r["batter_id"]): str(r["team"])
               for r in recent.to_dict("records")}
    name_of = {str(r["batter_id"]): str(r["batter_name"])
               for r in recent.to_dict("records")}

    today = date.today()
    tomorrow = today + timedelta(days=1)
    eligible = apply_confirmed_cards(slot_of, team_of, str(today), str(tomorrow))
    probables = fetch_probables(str(today), str(tomorrow))
    rows = []
    for (home, away, _k), (home_sp, away_sp) in probables.items():
        for team, opposing_sp in ((home, away_sp), (away, home_sp)):
            for pid, batter_team in team_of.items():
                if batter_team != team:
                    continue
                if eligible is not None and pid not in eligible:
                    continue
                expected = model.expected_home_runs(
                    pid, opposing_starter=opposing_sp, venue=home,
                    lineup_slot=slot_of.get(pid))
                if expected is None:
                    continue
                rows.append({"player_id": pid, "player_name": name_of.get(pid, pid),
                             "team": team, "position": None,
                             "points": round(float(expected), 4)})
    return pd.DataFrame(rows, columns=["player_id", "player_name", "team",
                                       "position", "points"])


# FantasyPros stat keys that are a touchdown SCORED. A quarterback's passing
# touchdowns are thrown, not scored, so they are deliberately absent — DK's
# Single Stat - Touchdowns pays the player who reaches the end zone.
_TD_STATS = ("rush_tds", "rec_tds")


def _football_touchdowns(fp: pd.DataFrame) -> pd.DataFrame:
    """Expected touchdowns scored per player, from the consensus projections."""
    if fp.empty:
        return pd.DataFrame(columns=["player_name", "team", "position", "points"])
    df = fp.copy()
    df["stat"] = df["stat"].astype(str).str.lower().str.strip()
    df = df[df["stat"].isin(_TD_STATS)]
    if df.empty:
        return pd.DataFrame(columns=["player_name", "team", "position", "points"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0.0)
    return (
        df.groupby("player_name", dropna=True)
        .agg(player_id=("player_id", "first"), team=("team", "first"),
             position=("position", "first"), points=("value", "sum"))
        .reset_index()[["player_id", "player_name", "team", "position", "points"]]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build DK Tiers / Single Stat entries")
    parser.add_argument("--tiered", required=True,
                        help="dk_tiered_{league}_{stamp}.parquet from the collector")
    parser.add_argument("--league", default="mlb")
    parser.add_argument("--fp", default=None,
                        help="FantasyPros long frame (football Single Stat)")
    parser.add_argument("--out", required=True, help="output folder")
    args = parser.parse_args()

    from velocity.dfs.optimizer import lineup_pool
    from velocity.dfs.pipeline import eligible_board, game_time_ct, normalize_positions
    from velocity.dfs.tiered import TIER_SPECS, build_tier_entry, tier_frame
    from velocity.report.dfs_png import render_tier_card, tier_caption

    boards = pd.read_parquet(args.tiered)
    if "league" in boards.columns:
        boards = boards[boards["league"] == args.league]
    if boards.empty or "game_type" not in boards.columns:
        print("no tiered boards in the snapshot")
        return
    playable = boards[boards["game_type"].astype(str).isin(_SUPPORTED)]
    if playable.empty:
        print("no Tiers or Single Stat board on the snapshot "
              f"(saw: {sorted(boards['game_type'].dropna().unique())})")
        return

    fp = pd.read_parquet(args.fp) if args.fp else pd.DataFrame()
    if not fp.empty and "league" in fp.columns:
        fp = fp[fp["league"] == args.league]

    points_cache: dict[str, pd.DataFrame] = {}

    def points_for(game_type: str) -> pd.DataFrame:
        if game_type not in points_cache:
            if game_type == "Tiers":
                points_cache[game_type] = _mlb_dk_points()
            elif game_type == "Single Stat - Home Runs":
                points_cache[game_type] = _mlb_home_runs()
            else:
                points_cache[game_type] = _football_touchdowns(fp)
        return points_cache[game_type]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    frames: list[pd.DataFrame] = []
    for gid, board in playable.groupby(playable["draft_group_id"].astype(str)):
        game_type = str(board["game_type"].iloc[0])
        spec = TIER_SPECS[game_type]
        try:
            points = points_for(game_type)
        except Exception as exc:  # noqa: BLE001 - one format never blocks another
            print(f"{game_type}: projections unavailable ({exc}); skipping")
            continue
        if points.empty:
            print(f"{game_type}: no projections; skipping")
            continue
        eligible = eligible_board(normalize_positions(board, spec), spec)
        pool = lineup_pool(eligible, points)
        entry = build_tier_entry(pool, spec=spec) if not pool.empty else None
        if entry is None:
            print(f"{game_type} (group {gid}): no entry "
                  f"({len(eligible)} on the board, {len(pool)} projected)")
            continue
        unit = "HR" if spec.stat == "home_runs" else (
            "TD" if spec.stat == "touchdowns" else "DK pts")
        print(f"\n=== {game_type} (group {gid}) — "
              f"{len(pool)}/{len(eligible)} projected ===")
        for pick in entry.picks:
            print(f"  {pick.slot:5} {pick.player_name:24} {pick.team or '—':4} "
                  f"{pick.points:7.3f} {unit}  {game_time_ct(pick.kickoff)}")
        print(f"  total {entry.total_points:.3f} {unit}")
        frames.append(tier_frame(entry, gid).assign(
            game_type=game_type, unit=unit,
            game_time=lambda f: f["kickoff"].map(game_time_ct)))

        label = f"DK {game_type.upper()}"
        slug = spec.name
        when = datetime.now(UTC).strftime("%A, %b %-d").upper()
        source = {
            "Tiers": "contextual model (docs/DFS_MODEL.md) scored as DK points",
            "Single Stat - Home Runs":
                "empirical-Bayes home-run model (docs/PROPS_HR.md)",
            "Single Stat - Touchdowns":
                "FantasyPros consensus rushing + receiving touchdowns",
        }[game_type]
        card = out / f"dfs_{slug}_{stamp}.png"
        render_tier_card(entry, card, when=when, slate_label=label, unit=unit,
                         source_note=source)
        print(f"  rendered card to {card}")
        (out / f"dfs_{slug}_{stamp}_captions.md").write_text(
            tier_caption(entry, slate_label=label.lower(), unit=unit) + "\n")

    if not frames:
        print("no tiered entry built")
        return
    dest = out / f"dfs_tiered_{args.league}_{stamp}.parquet"
    pd.concat(frames, ignore_index=True).to_parquet(dest, index=False)
    print(f"\nwrote {len(frames)} tiered entr(ies) to {dest}")


if __name__ == "__main__":
    main()
