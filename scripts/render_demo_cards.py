"""Render matchup cards for a historical week — the app-preview demo slate.

Fits the league's promoted model on the committed datasets (exactly the live
runner's fit) and renders MARKET vs MODEL cards for one past week, using the
schedule's real closing spread/total as the MARKET row — genuine numbers,
nothing fabricated. Moneylines aren't in the committed schedules, so the win-
prob market cell honestly renders "—".

Outputs the same files the live runner persists for cards (PNGs, captions,
games map, projections frame) under a current stamp, so the plays app's Cards
and Matchups tabs pick them up from a normal ``slate-*`` artifact. No slate/
props/parlays parquets are written — the day-after grader finds zero plays in
a demo artifact and records nothing.

    python scripts/render_demo_cards.py --league nfl --data datasets/nfl \
        --season 2025 --week 10 --out artifacts/slate
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))


def demo_lines(week_games: pd.DataFrame) -> pd.DataFrame:
    """The schedule's closing numbers as a card-consumable lines frame.

    nflverse/CFBD convention: ``spread_line`` positive = home favored, so the
    canonical home point (negative = home favored) is its negation. One
    synthetic book label; timestamp = kickoff (the close).
    """
    rows: list[dict[str, object]] = []
    for game in week_games.to_dict("records"):
        gid = str(game["game_id"])
        kickoff = game.get("kickoff")
        spread = game.get("spread_line")
        if spread is not None and not pd.isna(spread):
            rows.append({"game_id": gid, "market": "spread", "side": "home",
                         "price": -110, "point": -float(spread),
                         "book": "closing line", "timestamp": kickoff})
        total = game.get("total_line")
        if total is not None and not pd.isna(total):
            rows.append({"game_id": gid, "market": "total", "side": "over",
                         "price": -110, "point": float(total),
                         "book": "closing line", "timestamp": kickoff})
    return pd.DataFrame(
        rows, columns=["game_id", "market", "side", "price", "point", "book",
                       "timestamp"]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Render demo cards for a past week")
    parser.add_argument("--league", required=True, choices=["nfl", "ncaaf"])
    parser.add_argument("--data", required=True, help="committed dataset folder")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--n-sims", type=int, default=20_000)
    parser.add_argument("--max-games", type=int, default=8,
                        help="cap the card count (earliest kickoffs first)")
    parser.add_argument("--teams", default=None,
                        help="comma-separated team names; keep games involving any")
    args = parser.parse_args()

    import run_live_slate as runner  # same folder; reuses the promoted fits
    from velocity.ingest.local import load_games
    from velocity.report.social import build_social_cards
    from velocity.report.social_png import render_cards

    fit_args = SimpleNamespace(league=args.league, data=args.data, n_sims=args.n_sims)
    project, teams, _ratings = runner._build_projection(fit_args)
    known = set(teams)

    games = load_games(runner._find_games(Path(args.data)), league=args.league)
    week = games[(games["season"] == args.season) & (games["week"] == args.week)]
    if args.teams:
        wanted = {t.strip() for t in args.teams.split(",") if t.strip()}
        week = week[week["home_team"].isin(wanted) | week["away_team"].isin(wanted)]
    week = week.sort_values("kickoff").head(args.max_games)
    if week.empty:
        raise SystemExit(f"no {args.league} games for season {args.season} week {args.week}")

    events = week[["game_id", "home_team", "away_team", "kickoff"]].copy()
    events["game_id"] = events["game_id"].astype(str)
    projections = {}
    for game in events.to_dict("records"):
        home, away = str(game["home_team"]), str(game["away_team"])
        if home not in known or away not in known:
            print(f"  skipped {away} @ {home} (team not in the fit)")
            continue
        projections[str(game["game_id"])] = project(home, away)
    print(f"{args.league}: projected {len(projections)} of {len(events)} games "
          f"(season {args.season}, week {args.week})")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    asset_dir = out / ".assets"
    aliases = None
    team_colors = None
    code_to_team: dict[str, str] = {}
    if args.league == "ncaaf":
        aliases, team_colors, code_to_team = runner._ncaaf_identity(events, asset_dir)

    cards = build_social_cards(
        projections, events, lines=demo_lines(week),
        aliases=aliases, team_colors=team_colors,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    paths = render_cards(cards, out, stamp, asset_dir=asset_dir, league=args.league)
    print(f"rendered {len(paths)} demo card(s) to {out}")

    # The Deep Dive companions, from the same committed data.
    from velocity.ingest.local import load_plays
    from velocity.report.deepdive import build_deep_dives
    from velocity.report.deepdive_png import render_deep_dives

    plays_path = runner._find_plays(Path(args.data)) if args.league == "nfl" else None
    plays = load_plays(plays_path) if plays_path is not None else None
    dives = build_deep_dives(cards, projections, games, plays,
                             team_names=code_to_team)
    dive_paths = render_deep_dives(dives, out, stamp, asset_dir=asset_dir,
                                   league=args.league)
    print(f"rendered {len(dive_paths)} demo deep dive(s) to {out}")

    # Compose the sheets exactly like the live runner (one graphic per game;
    # the intermediates are consumed) so previews match the real artifact.
    from velocity.report.sheet_png import compose_sheets

    card_by_game = {str(c.game_id): p for c, p in zip(cards, paths, strict=True)}
    dive_by_game = {str(d.card.game_id): p
                    for d, p in zip(dives, dive_paths, strict=True)}
    sheets = compose_sheets(card_by_game, dive_by_game, out)
    social_captions = out / f"social_{args.league}_{stamp}_captions.md"
    if social_captions.exists():
        social_captions.rename(out / f"sheet_{args.league}_{stamp}_captions.md")
    for stale in (*card_by_game.values(), *dive_by_game.values()):
        stale.unlink(missing_ok=True)
    (out / f"deepdive_{args.league}_{stamp}_captions.md").unlink(missing_ok=True)
    pd.DataFrame(
        [{"game_id": gid, "kind": "sheet", "file": p.name}
         for gid, p in sheets.items()]
    ).assign(league=args.league).to_parquet(
        out / f"cardindex_{args.league}_{stamp}.parquet", index=False
    )
    print(f"composed {len(sheets)} demo sheet(s)")

    # The Matchups tab's frames — but deliberately no slate/props/parlays, so
    # the grader sees zero plays and the record chain stays untouched.
    events.assign(league=args.league).to_parquet(
        out / f"games_{args.league}_{stamp}.parquet", index=False
    )
    runner._projections_frame(projections).assign(
        league=args.league, generated_at=pd.Timestamp(datetime.now(UTC)).tz_localize(None)
    ).to_parquet(out / f"projections_{args.league}_{stamp}.parquet", index=False)


if __name__ == "__main__":
    main()
