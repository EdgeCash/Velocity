"""Grade the most recent prior day's slate → the email's model-status record.

The workflow downloads previous runs' private artifacts into a folder; this
script finds the **latest slate from the most recent prior date** (US/Central —
the operator's day, so both game-day runs report the same finished day), grades
it against the league's schedule feed, and writes
``record_<league>_<stamp>.parquet`` into the slate output folder, where the
email renderer picks it up.

Grading sources (all free):
* NFL: nflverse schedules (finals land within hours of the game),
* NCAAF: CFBD games (needs ``CFBD_API_KEY`` in the environment).

Game markets and parlay game legs grade against finals; player props return
with the football prop slate (docs/FOOTBALL_CUTOVER.md Phase 3). Anything that
cannot be graded honestly stays ``pending``. Every failure mode (no prior
slate, empty day, a feed down) exits 0 — the record section is additive and
must never block the slate email.

    python scripts/grade_yesterday.py --prev-dir artifacts/previous \
        --out-dir artifacts/slate --league nfl
"""

from __future__ import annotations

import argparse
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

_STAMP = r"(\d{8}T\d{6}Z)"
_OPERATOR_TZ = ZoneInfo("America/Chicago")


def _stamps(prev_dir: Path, league: str) -> dict[str, dict[str, Path]]:
    """All persisted frames under ``prev_dir``, grouped by run stamp.

    Returns ``{stamp: {kind: path}}`` for kinds ``slate``/``parlays``/``games``;
    a newer duplicate of the same (stamp, kind) wins arbitrarily — they are
    identical files from the same run.
    """
    lg = re.escape(league)
    patterns = {
        "slate": rf"slate_{lg}_{_STAMP}\.parquet",
        "props": rf"slate_{lg}_props_{_STAMP}\.parquet",
        "parlays": rf"slate_{lg}_parlays_{_STAMP}\.parquet",
        "games": rf"games_{lg}_{_STAMP}\.parquet",
        "projections": rf"projections_{lg}_{_STAMP}\.parquet",
        "distributions": rf"distributions_{lg}_{_STAMP}\.parquet",
    }
    out: dict[str, dict[str, Path]] = {}
    for path in prev_dir.rglob("*.parquet"):
        for kind, pattern in patterns.items():
            match = re.fullmatch(pattern, path.name)
            if match:
                out.setdefault(match.group(1), {})[kind] = path
    return out


def _pick_prior_stamp(stamps: dict[str, dict[str, Path]], now_utc: datetime) -> str | None:
    """The latest stamp whose operator-local date precedes today's."""
    today = now_utc.astimezone(_OPERATOR_TZ).date()
    prior = [
        s for s in stamps
        if datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        .astimezone(_OPERATOR_TZ).date() < today
    ]
    return max(prior) if prior else None


def _load(paths: dict[str, Path], kind: str) -> pd.DataFrame | None:
    path = paths.get(kind)
    return pd.read_parquet(path) if path is not None else None


def closing_for_slate(
    odds_dir: Path, slate: pd.DataFrame, games_map: pd.DataFrame, league: str
) -> pd.DataFrame | None:
    """The consensus closing line per bet key, from the hourly odds archive.

    Reads every ``odds_lines_*.parquet`` snapshot under ``odds_dir``, keeps
    this league's rows for the slate's games, canonicalizes provider side
    labels against the slate's own games map, takes the honest close (last
    pre-kickoff observation per game/market/side/book), and reduces across
    books to a median-consensus ``price``/``point`` per (game_id, market,
    side) — the exact key ``grade_slate`` matches CLV on. Returns ``None``
    when the archive holds nothing for these games.
    """
    from velocity.store.pit import closing_line
    from velocity.wagering.live import canonicalize_sides

    snapshots = sorted(odds_dir.rglob("odds_lines_*.parquet"))
    if not snapshots:
        return None
    game_ids = set(slate["game_id"].astype(str))
    frames = []
    for path in snapshots:
        snap = pd.read_parquet(path)
        if "league" in snap.columns:
            snap = snap[snap["league"] == league]
        snap = snap[snap["game_id"].astype(str).isin(game_ids)]
        if not snap.empty:
            frames.append(snap)
    if not frames:
        return None
    lines = pd.concat(frames, ignore_index=True)
    lines = canonicalize_sides(lines, games_map)
    if lines.empty:
        return None
    per_book = closing_line(lines, games_map)
    if per_book.empty:
        return None
    closing = (per_book.groupby(["game_id", "market", "side"], as_index=False)
               .agg(price=("price", "median"), point=("point", "median")))
    return closing


def _newest_cumulative(prev_dir: Path, league: str) -> pd.DataFrame | None:
    """The season record chain from the newest downloaded artifact carrying one."""
    pattern = rf"cumulative_record_{re.escape(league)}_{_STAMP}\.parquet"
    matches = sorted(
        (p for p in prev_dir.rglob("*.parquet") if re.fullmatch(pattern, p.name)),
        key=lambda p: p.name,
    )
    return pd.read_parquet(matches[-1]) if matches else None


def _load_schedule(
    league: str, season: int, slate_date: datetime | None = None
) -> pd.DataFrame | None:  # pragma: no cover
    """The league's ``Games``-shaped schedule (with finals), or ``None`` on failure.

    The summer leagues (mlb/wnba) fetch only a narrow window around the graded
    date from their free keyless feeds — grading needs one day's finals, not a
    season crawl.
    """
    try:
        if league == "nfl":
            from velocity.ingest.nfl import load_schedules

            return load_schedules([season])
        if league == "ncaaf":
            api_key = os.environ.get("CFBD_API_KEY", "")
            if not api_key:
                print("CFBD_API_KEY not set; NCAAF finals unavailable")
                return None
            from velocity.ingest.ncaaf import load_games

            return load_games([season], api_key)
        if league == "mlb" and slate_date is not None:
            from datetime import timedelta

            from build_inseason_datasets import _MLB_URL, _get
            from velocity.ingest.inseason import normalize_mlb_schedule

            # One statsapi range call around the graded day — grading needs a
            # day's finals, not a season crawl.
            day = slate_date.date()
            payload = _get(_MLB_URL.format(
                start=(day - timedelta(days=2)).isoformat(),
                end=(day + timedelta(days=1)).isoformat(),
            ))
            return normalize_mlb_schedule(payload, season)
        if league == "wnba":
            from datetime import date as _date

            from build_inseason_datasets import fetch_wnba_season

            # The wehoop release parquet is one small season file — the
            # CI-reachable transport (ESPN's API 403s datacenter IPs).
            return fetch_wnba_season(season, _date.today())
        if league == "nhl" and slate_date is not None:
            from datetime import timedelta

            from velocity.ingest.hockey import (
                fetch_json,
                normalize_day_scores,
                score_day_url,
            )

            # Finals straight from the NHL API, one call per day around the
            # graded date.
            day = slate_date.date()
            frames = [
                normalize_day_scores(fetch_json(score_day_url(
                    (day + timedelta(days=offset)).isoformat())))
                for offset in (-1, 0, 1)
            ]
            finals = pd.concat([f for f in frames if not f.empty],
                               ignore_index=True)
            return finals.drop_duplicates("game_id") if not finals.empty else None
        if league == "ncaab" and slate_date is not None:
            from velocity.ingest.ncaab import load_hoopr_schedule

            # hoopR mirrors the wehoop transport (raw-CDN parquet, CI-safe).
            # A November slate belongs to the season labeled by the NEXT
            # calendar year (season 2026 = 2025-26).
            ncaab_season = season + 1 if slate_date.month >= 8 else season
            return load_hoopr_schedule(ncaab_season)
    except Exception as exc:  # noqa: BLE001 - a feed down never blocks the slate email
        print(f"schedule fetch failed ({exc})")
    return None


def _aliases_for(
    league: str,
    schedule: pd.DataFrame,
    provider_names: set[str] | None = None,
) -> dict[str, str]:
    """Provider-name → schedule-team aliases for the finals bridge.

    NFL uses the fixed alias table (full names → nflverse codes). Elsewhere
    the schedule teams key themselves (exact/normalized matches pass), and
    the slate's provider names — which carry nicknames in the college
    leagues ("Duke Blue Devils" vs the schedule's "Duke") — bridge by the
    same prefix rule the live slate resolves with. Anything unmatched stays
    pending, never guessed.
    """
    if league == "nfl":
        from velocity.wagering.live import NFL_TEAM_ALIASES

        return dict(NFL_TEAM_ALIASES)
    if league == "nhl":
        from velocity.ingest.hockey import NHL_TEAM_ALIASES

        return dict(NHL_TEAM_ALIASES)
    teams = set(schedule["home_team"].astype(str)) | set(schedule["away_team"].astype(str))
    aliases = {name: name for name in teams}
    if provider_names:
        from velocity.wagering.live import nickname_aliases

        aliases.update(nickname_aliases(provider_names, teams))
    return aliases


def _grade_mlb_props(  # pragma: no cover - network
    props: pd.DataFrame, slate_date: datetime
) -> pd.DataFrame | None:
    """Grade pitcher-K props against the day's statsapi boxscores.

    The same extractor that banks the starters dataset supplies the
    actuals: one boxscore call per game on the graded day, starter name →
    strikeouts. Missing players stay pending, never guessed.
    """
    try:
        from datetime import timedelta

        from build_mlb_pitching import (
            _BOX_URL,
            _SCHED_URL,
            _get,
            extract_starters,
        )
        from velocity.backtest.props_football import grade_prop_ledger

        day = slate_date.date()
        sched = _get(_SCHED_URL.format(start=(day - timedelta(days=1)).isoformat(),
                                       end=(day + timedelta(days=1)).isoformat()))
        pks = [str(g["gamePk"]) for d in sched.get("dates", [])
               for g in d.get("games", [])]
        rows = []
        for pk in pks:
            try:
                rows.extend(extract_starters(_get(_BOX_URL.format(pk=pk)), pk))
            except Exception:  # noqa: BLE001 - one bad boxscore never blocks
                continue
        if not rows:
            return None
        actuals = (pd.DataFrame(rows)
                   .rename(columns={"starter_name": "player_name",
                                    "k": "pitcher_strikeouts"})
                   [["player_name", "pitcher_strikeouts"]])
        return grade_prop_ledger(props, actuals)
    except Exception as exc:  # noqa: BLE001 - grading never blocks the record
        print(f"MLB prop grading skipped ({exc})")
        return None


def _grade_props(  # pragma: no cover - network
    league: str,
    props: pd.DataFrame | None,
    schedule: pd.DataFrame,
    slate_date: datetime,
) -> pd.DataFrame | None:
    """Grade the prop slate against nflverse weekly actuals (NFL only).

    The slate's date pins the NFL week via the schedule (kickoffs within a day
    of the slate); the weekly stats for those weeks are the actuals. Any
    failure leaves props ungraded rather than blocking the record.
    """
    if props is None or props.empty or league not in ("nfl", "mlb"):
        return None
    if league == "mlb":
        return _grade_mlb_props(props, slate_date)
    try:
        from velocity.backtest.props_football import grade_prop_ledger
        from velocity.ingest.nfl import load_weekly_stats

        kick = pd.to_datetime(schedule["kickoff"]).dt.normalize()
        target = pd.Timestamp(slate_date.date())
        window = schedule[(kick - target).abs() <= pd.Timedelta(days=1)]
        weeks = set(window["week"].dropna().astype(int))
        if not weeks:
            print("prop grading skipped: no schedule games near the slate date")
            return None
        season = int(window["season"].iloc[0])
        weekly = load_weekly_stats([season])
        weekly = weekly[weekly["week"].isin(weeks)]
        return grade_prop_ledger(props, weekly)
    except Exception as exc:  # noqa: BLE001 - props grading never blocks the record
        print(f"prop grading skipped ({exc})")
        return None


def main() -> None:  # pragma: no cover - network orchestration (pure parts live in report/)
    parser = argparse.ArgumentParser(description="Grade the previous day's slate")
    parser.add_argument("--prev-dir", required=True, help="downloaded previous artifacts")
    parser.add_argument("--out-dir", required=True, help="folder to write the record parquet")
    parser.add_argument("--league", default="nfl",
                        choices=["nfl", "ncaaf", "mlb", "wnba", "ncaab", "nhl"])
    parser.add_argument("--odds-dir", default="artifacts/odds",
                        help="downloaded odds-lines snapshots (the CLV close source)")
    args = parser.parse_args()

    from velocity.report.daily_record import (
        accumulate_record,
        build_daily_record,
        empty_record,
        grade_parlay_frame,
        record_headline,
    )
    from velocity.report.results import finals_for_slate
    from velocity.report.scorecard import grade_slate

    now = datetime.now(UTC)
    stamps = _stamps(Path(args.prev_dir), args.league)
    stamp = _pick_prior_stamp(stamps, now)
    if stamp is None:
        print("no prior-day slate found in the downloaded artifacts; skipping record")
        return
    paths = stamps[stamp]
    slate = _load(paths, "slate")
    props = _load(paths, "props")
    parlays = _load(paths, "parlays")
    games_map = _load(paths, "games")
    n_plays = sum(0 if f is None else len(f) for f in (slate, props, parlays))
    print(f"grading slate {stamp}: {n_plays} play(s)")

    slate_date = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
    record = None
    finals = None
    if n_plays == 0 or games_map is None or games_map.empty:
        record = empty_record()
        record["slate_date"] = pd.Timestamp(slate_date)
    else:
        schedule = _load_schedule(args.league, slate_date.year, slate_date)
        if schedule is None:
            record = empty_record()
            record["slate_date"] = pd.Timestamp(slate_date)
        else:
            aliases = _aliases_for(
                args.league, schedule,
                provider_names=set(games_map["home_team"].astype(str))
                | set(games_map["away_team"].astype(str)),
            )
            finals = finals_for_slate(games_map, schedule, aliases=aliases)
            # The closing lines from the hourly odds archive — best-effort;
            # bets with no matched close grade normally with CLV left null.
            closing = None
            if slate is not None and not slate.empty:
                try:
                    closing = closing_for_slate(
                        Path(args.odds_dir), slate, games_map, args.league)
                    matched = 0 if closing is None else len(closing)
                    print(f"closing lines: {matched} (game, market, side) keys matched")
                except Exception as exc:  # noqa: BLE001 - CLV never blocks grading
                    print(f"closing lines skipped ({exc})")
            games_graded = (None if slate is None or slate.empty
                            else grade_slate(slate, finals, closing))
            props_graded = _grade_props(args.league, props, schedule, slate_date)
            parlays_graded = (
                None if parlays is None or parlays.empty
                else grade_parlay_frame(parlays, finals)
            )
            matchups = {
                str(r["game_id"]): f"{r['away_team']} @ {r['home_team']}"
                for r in games_map.to_dict("records")
            }
            record = build_daily_record(
                games_graded, props_graded, parlays_graded,
                matchups=matchups, slate_date=slate_date,
            )

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_stamp = now.strftime("%Y%m%dT%H%M%SZ")
    dest = out / f"record_{args.league}_{out_stamp}.parquet"
    record.to_parquet(dest, index=False)
    print(record_headline(record))
    print(f"wrote {len(record)} graded row(s) to {dest}")

    # Season chain: fold the day's SETTLED plays into the newest cumulative
    # record and carry it forward in this run's artifact (the next run downloads
    # it and continues). Pending rows never enter the chain: a bet the chain
    # can't settle today is never revisited (only the latest prior-date slate is
    # ever graded), so a pending row would be permanent noise — the first live
    # run proved it by folding 715 stale future-game bets into the season line.
    # Filtering the downloaded chain too heals any history that already
    # carries them.
    prior = _newest_cumulative(Path(args.prev_dir), args.league)
    if prior is not None and "result" in prior.columns:
        prior = prior[prior["result"] != "pending"]
    settled_record = record[record["result"] != "pending"] if not record.empty else record
    cumulative = accumulate_record(prior, settled_record)
    cumulative.to_parquet(
        out / f"cumulative_record_{args.league}_{out_stamp}.parquet", index=False
    )
    print(f"season record: {len(cumulative)} settled row(s) accumulated")

    # Post-game graphics — the Sim Check cards (actual result on the pregame
    # distribution) and the model record card. Best-effort: rendering trouble
    # never blocks the graded record itself.
    try:
        from velocity.report.sim_check import build_sim_checks
        from velocity.report.social_png import render_record_card, render_sim_checks

        projections_frame = _load(paths, "projections")
        distributions = _load(paths, "distributions")
        if (
            projections_frame is not None
            and distributions is not None
            and finals is not None
            and games_map is not None
        ):
            checks = build_sim_checks(projections_frame, distributions, finals, games_map)
            rendered = render_sim_checks(checks, out, out_stamp,
                                         asset_dir=out / ".assets", league=args.league)
            print(f"rendered {len(rendered)} sim check card(s)")
            if rendered:
                # game_id → filename manifest (paths align with checks) so
                # the site can pin each card to its matchup page.
                pd.DataFrame(
                    [{"game_id": str(check.game_id), "kind": "simcheck",
                      "file": path.name}
                     for check, path in zip(checks, rendered, strict=True)]
                ).assign(league=args.league).to_parquet(
                    out / f"cardindex_{args.league}_{out_stamp}.parquet",
                    index=False,
                )
        settled = record[record["result"].isin(["win", "loss", "push"])]
        if not settled.empty:
            when = record["slate_date"].dropna()
            date_label = "" if when.empty else pd.Timestamp(when.iloc[0]).strftime("%b %-d")
            card_dest = out / f"recordcard_{args.league}_{out_stamp}.png"
            render_record_card(record, cumulative, card_dest, date_label=date_label)
            print(f"rendered model record card to {card_dest}")
    except Exception as exc:  # noqa: BLE001
        print(f"post-game cards skipped: {exc}")


if __name__ == "__main__":
    main()
