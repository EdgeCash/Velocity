"""Render the weekend broadcast grid from the run's own artifacts.

Assembles the Saturday (NCAAF) or Sunday (NFL) grid — every game as a
network-row block with the consensus spread/total strip — from data the
pipeline already has in hand: the banked games map for kickoffs, the hourly
odds archive for consensus lines, CFBD's media listing for college networks
(``CFBD_API_KEY``; absent, rows fall back to kickoff windows), and the
identity tables for codes and colors. No new odds spend, no marks, no picks.

    python scripts/render_broadcast_grid.py --league ncaaf \
        --slate-dir artifacts/slate --odds-dir artifacts/odds --out artifacts/slate
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.report.assets import (
    TEAM_META,
    league_logo_path,
    logo_path,
    ncaaf_logo_path,
    ncaaf_team_index,
)
from velocity.report.broadcast_grid import (
    GridGame,
    consensus_line_text,
    eastern,
    normalize_media,
    render_grid,
    window_label,
)
from velocity.wagering.live import NFL_TEAM_ALIASES, nickname_aliases

_STAMP = r"(\d{8}T\d{6}Z)"
_TARGET_WEEKDAY = {"ncaaf": 5, "nfl": 6}  # Saturday / Sunday
_DURATION = {"ncaaf": 3.5, "nfl": 3.25}


def newest(folder: Path, pattern: str) -> Path | None:
    matches = sorted(p for p in folder.rglob("*") if re.fullmatch(pattern, p.name))
    return matches[-1] if matches else None


def target_date(league: str, today: pd.Timestamp) -> pd.Timestamp:
    """The next Saturday (ncaaf) / Sunday (nfl) in Eastern time, today included."""
    ahead = (_TARGET_WEEKDAY[league] - today.weekday()) % 7
    return (today + pd.Timedelta(days=ahead)).normalize()


def consensus_by_game(lines: pd.DataFrame, games: pd.DataFrame) -> dict[str, dict[str, float]]:
    """``{game_id: {"spread_home": x, "total": y}}`` from an odds snapshot."""
    from velocity.wagering.live import canonicalize_sides

    canon = canonicalize_sides(lines, games)
    if canon.empty:
        return {}
    stamp_col = "collected_at" if "collected_at" in canon.columns else "timestamp"
    canon[stamp_col] = pd.to_datetime(canon[stamp_col])
    canon = canon[canon[stamp_col] == canon.groupby("game_id")[stamp_col].transform("max")]
    out: dict[str, dict[str, float]] = {}
    spreads = canon[(canon["market"] == "spread") & (canon["side"] == "home")]
    for gid, grp in spreads.groupby("game_id"):
        out.setdefault(str(gid), {})["spread_home"] = float(grp["point"].median())
    totals = canon[(canon["market"] == "total") & (canon["side"] == "over")]
    for gid, grp in totals.groupby("game_id"):
        out.setdefault(str(gid), {})["total"] = float(grp["point"].median())
    return out


def fetch_media(year: int, key: str) -> list[dict]:  # pragma: no cover - network
    """CFBD ``/games/media`` for the season (regular), raw rows."""
    req = urllib.request.Request(
        f"https://api.collegefootballdata.com/games/media?year={year}&seasonType=regular",
        headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read())


def build_games(  # noqa: PLR0912 - one assembly seam, league branches inline
    league: str,
    day: pd.DataFrame,
    consensus: dict[str, dict[str, float]],
    media: dict[tuple[str, str], str],
    asset_dir: Path | None = None,
) -> list[GridGame]:
    """The grid blocks: identity, marks, network row (or window), fact strip."""
    if league == "nfl":
        codes = dict(NFL_TEAM_ALIASES.items())
        colors = {code: meta.color for code, meta in TEAM_META.items()}
        to_code = lambda name: codes.get(str(name), str(name)[:3].upper())  # noqa: E731
        to_logo = lambda name: logo_path(to_code(name), asset_dir)  # noqa: E731
        to_school = {str(n): str(n) for n in
                     set(day["home_team"]) | set(day["away_team"])}
    else:
        index = ncaaf_team_index(os.environ.get("CFBD_API_KEY"), cache_dir=asset_dir)
        aliases = nickname_aliases(
            set(day["home_team"]) | set(day["away_team"]), index.keys())
        to_school = {name: aliases.get(name, name) for name in
                     set(day["home_team"].astype(str)) | set(day["away_team"].astype(str))}
        codes = {school: meta.abbreviation for school, meta in index.items()}
        colors = {meta.abbreviation: meta.color or "" for meta in index.values()}
        to_code = lambda name: codes.get(  # noqa: E731
            to_school.get(str(name), str(name)), str(name)[:3].upper())
        to_logo = lambda name: ncaaf_logo_path(  # noqa: E731
            getattr(index.get(to_school.get(str(name), "")), "espn_id", None),
            asset_dir)

    blocks: list[GridGame] = []
    for rec in day.to_dict("records"):
        kickoff_et = eastern(rec["kickoff"])
        away, home = to_code(rec["away_team"]), to_code(rec["home_team"])
        row = media.get((to_school.get(str(rec["home_team"]), ""),
                         to_school.get(str(rec["away_team"]), ""))) \
            or window_label(kickoff_et)
        numbers = consensus.get(str(rec["game_id"]), {})
        blocks.append(GridGame(
            row=str(row), away=away, home=home, kickoff_et=kickoff_et,
            away_color=colors.get(away) or None,
            home_color=colors.get(home) or None,
            line_text=consensus_line_text(
                away, home, numbers.get("spread_home"), numbers.get("total")),
            away_logo=to_logo(rec["away_team"]),
            home_logo=to_logo(rec["home_team"]),
        ))
    return blocks


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekend broadcast grid PNG")
    parser.add_argument("--league", choices=["ncaaf", "nfl"], required=True)
    parser.add_argument("--slate-dir", default="artifacts/slate")
    parser.add_argument("--odds-dir", default="artifacts/odds")
    parser.add_argument("--out", default="artifacts/slate")
    parser.add_argument("--date", help="ET date YYYY-MM-DD (default: next Sat/Sun)")
    parser.add_argument("--asset-dir",
                        help="logo/identity cache (default: <out>/.assets, "
                             "the runner's convention)")
    args = parser.parse_args()

    slate_dir, odds_dir = Path(args.slate_dir), Path(args.odds_dir)
    games_path = newest(slate_dir, rf"games_{args.league}_{_STAMP}\.parquet")
    if games_path is None:
        raise SystemExit(f"no games_{args.league} artifact under {slate_dir}/")
    games = pd.read_parquet(games_path)
    games["game_id"] = games["game_id"].astype(str)

    now_et = eastern(pd.Timestamp.now(tz="UTC").tz_localize(None))
    day_date = (pd.Timestamp(args.date) if args.date
                else target_date(args.league, now_et))
    kick_et = games["kickoff"].map(eastern)
    day = games[kick_et.dt.date == day_date.date()].copy()
    if day.empty:
        raise SystemExit(f"no {args.league} games on {day_date.date()} in {games_path.name}")

    consensus: dict[str, dict[str, float]] = {}
    lines_path = newest(odds_dir, rf"odds_lines_{_STAMP}\.parquet") \
        if odds_dir.exists() else None
    if lines_path is not None:
        consensus = consensus_by_game(pd.read_parquet(lines_path), games)

    media: dict[tuple[str, str], str] = {}
    key = os.environ.get("CFBD_API_KEY", "")
    if args.league == "ncaaf" and key:
        try:
            media = normalize_media(fetch_media(int(day_date.year), key))
        except Exception as exc:  # noqa: BLE001 - networks are a nicety
            print(f"media listing skipped ({exc}) — kickoff-window rows instead")

    asset_dir = Path(args.asset_dir) if args.asset_dir else Path(args.out) / ".assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    blocks = build_games(args.league, day, consensus, media, asset_dir)
    n_lined = sum(1 for b in blocks if b.line_text)
    day_name = day_date.strftime("%A").upper()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = Path(args.out) / f"grid_{args.league}_{stamp}.png"
    render_grid(
        blocks, dest,
        title=f"{args.league.upper()} {day_name} — {day_date.strftime('%b %-d').upper()}",
        subtitle="All times Eastern · consensus spread & total per game",
        duration_hours=_DURATION[args.league],
        league_logo=league_logo_path(args.league, asset_dir),
    )
    print(f"grid: {len(blocks)} games ({n_lined} with lines, "
          f"{len({b.row for b in blocks})} rows) → {dest}")
    caption = (f"Every {args.league.upper()} game this {day_name.title()} — "
               "what's on, when, with the consensus spread and total. "
               "All times ET. Not picks, just the map. 🗺️")
    dest.with_suffix(".txt").write_text(caption)


if __name__ == "__main__":
    main()
