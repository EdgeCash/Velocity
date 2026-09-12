"""Grade the most recent prior day's slate → the email's model-status record.

The workflow downloads previous runs' private artifacts into a folder; this
script finds **every slate from before today** (US/Central — the operator's
day), merges the day's cards so each play is graded once, grades them against
the league's schedule feed, and writes
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
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

_STAMP = r"(\d{8}T\d{6}Z)"
# Books whose close is a sharp yardstick when it is on the board.
SHARP_BOOKS = frozenset({"pinnacle"})
_OPERATOR_TZ = ZoneInfo("America/Chicago")

# Schedule feeds that write a LOCAL clock rather than UTC. nflverse builds its
# kickoff by pasting the game date onto ``gametime``, which is Eastern, while
# the slate's kickoff is The Odds API's UTC ``commence_time``. Undeclared, that
# gap put every prime-time game on the next UTC day and it could never be
# graded (docs: velocity/report/results.py). Every other feed here reports UTC:
# CFBD's start_date, statsapi's gameDate, the NHL API and the hoopR/wehoop
# release parquets all do.
SCHEDULE_TZ_BY_LEAGUE = {"nfl": "America/New_York"}


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
        "portfolio": rf"portfolio_{lg}_{_STAMP}\.parquet",
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
    prior = prior_stamps(stamps, now_utc)
    return prior[-1] if prior else None


def prior_stamps(stamps: dict[str, dict[str, Path]], now_utc: datetime) -> list[str]:
    """Every stamp from before today, oldest first.

    The slate runs twice a day. Grading only the newest of them — which is what
    taking ``max`` did — meant the earlier card was never settled: its bets
    stayed pending forever, its closing-line value was never measured, and the
    games it uniquely covered (the ones already under way by the time the later
    run built its board, so dropped from that board) left the record entirely.
    Both cards are graded now, and the day's frames merged before grading.
    """
    today = now_utc.astimezone(_OPERATOR_TZ).date()
    return sorted(
        s for s in stamps
        if datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        .astimezone(_OPERATOR_TZ).date() < today
    )


# What makes two rows the same play, per persisted frame kind. A bet quoted on
# both of the day's cards is one bet, not two, and must not be graded twice.
_MERGE_KEYS: dict[str, tuple[str, ...]] = {
    "slate": ("game_id", "market", "side", "point", "book"),
    "props": ("game_id", "market", "side", "point", "book", "player"),
    "portfolio": ("game_id", "market", "side", "point", "book", "player"),
    "parlays": ("legs_json",),
    "games": ("game_id",),
    "projections": ("game_id",),
    "distributions": ("game_id",),
}


def merge_stamped(frames: list[pd.DataFrame], kind: str) -> pd.DataFrame | None:
    """Concatenate one day's frames of a kind, keeping each play once.

    The **earliest** appearance wins, which is the one the ledger actually
    placed: a bet first recommended on the afternoon card and repeated in the
    evening is booked at the afternoon price, so it has to be graded at that
    price too.
    """
    present = [f for f in frames if f is not None and not f.empty]
    if not present:
        return None
    out = pd.concat(present, ignore_index=True)
    keys = [k for k in _MERGE_KEYS.get(kind, ()) if k in out.columns]
    if not keys:
        return out.drop_duplicates().reset_index(drop=True)
    # ``point`` and ``player`` are nullable; a null must compare equal to a
    # null or every such row would survive as its own play.
    filled = out[keys].astype(object).where(out[keys].notna(), "\x00")
    return out[~filled.duplicated(keep="first")].reset_index(drop=True)


def _load(paths: dict[str, Path], kind: str) -> pd.DataFrame | None:
    path = paths.get(kind)
    return pd.read_parquet(path) if path is not None else None


def attach_sized_stakes(
    slate: pd.DataFrame | None, portfolio: pd.DataFrame | None, kind: str = "game"
) -> pd.DataFrame | None:
    """Join the portfolio card's sized stake onto a slate as ``stake_sized``.

    The slate parquet keeps the solo-Kelly stake for backtest comparability;
    the sized card (``portfolio_{league}_{stamp}``) is what the run actually
    recommended after the game, class, and slate caps. Rows the card never
    saw — paper rows, or a run before sizing existed — get 0 so the sized
    record counts them as watched, not wagered. A missing card leaves the
    column null: unknown, not zero.
    """
    if slate is None or slate.empty:
        return slate
    out = slate.copy()
    if portfolio is None or portfolio.empty or "stake" not in portfolio.columns:
        out["stake_sized"] = float("nan")
        return out
    card = portfolio
    if "kind" in card.columns:
        card = card[card["kind"].astype(str) == kind]
    keys = ["game_id", "market", "side"]
    if kind == "prop" and "player" in card.columns and "player" in out.columns:
        keys.append("player")
    sized = (card.drop_duplicates(subset=keys)[[*keys, "stake"]]
             .rename(columns={"stake": "stake_sized"}))
    out = out.drop(columns=["stake_sized"], errors="ignore").merge(sized, on=keys, how="left")
    out["stake_sized"] = pd.to_numeric(out["stake_sized"], errors="coerce").fillna(0.0)
    return out


def sized_profit(graded: pd.DataFrame | None) -> pd.DataFrame | None:
    """``profit_sized`` = the settled profit rescaled to the sized stake.

    Profit is linear in the stake (the same price, the same outcome), so the
    solo-stake grade converts exactly; a solo stake of zero (a paper row)
    contributes nothing at either size. Null ``stake_sized`` stays null.
    """
    if graded is None or graded.empty:
        return graded
    out = graded.copy()
    if "stake_sized" not in out.columns:
        out["stake_sized"] = float("nan")
        out["profit_sized"] = float("nan")
        return out
    stake = pd.to_numeric(out["stake"], errors="coerce")
    sized = pd.to_numeric(out["stake_sized"], errors="coerce")
    profit = pd.to_numeric(out["profit"], errors="coerce")
    ratio = (profit / stake).where(stake > 0, 0.0)
    out["profit_sized"] = (ratio * sized).where(sized.notna())
    return out


def attach_close_source(
    graded: pd.DataFrame | None, closing: pd.DataFrame | None
) -> pd.DataFrame | None:
    """Ride ``close_source`` (sharp / consensus) onto the graded game rows."""
    if graded is None or graded.empty:
        return graded
    out = graded.drop(columns=["close_source"], errors="ignore")
    if closing is None or closing.empty or "close_source" not in closing.columns:
        out["close_source"] = None
        return out
    keys = ["game_id", "market", "side"]
    src = closing.drop_duplicates(subset=keys)[[*keys, "close_source"]]
    return out.merge(src, on=keys, how="left")


def _carry_sized(
    graded: pd.DataFrame | None, slate: pd.DataFrame | None
) -> pd.DataFrame | None:
    """Re-attach ``stake_sized`` to a graded frame that dropped it.

    The graders rebuild their rows from :class:`Bet` tickets, which carry no
    sized stake; join it back by bet identity (with the player for props).
    """
    if graded is None or graded.empty or slate is None or "stake_sized" not in slate.columns:
        return graded
    if "stake_sized" in graded.columns:
        return graded
    keys = [k for k in ("game_id", "market", "side", "player")
            if k in graded.columns and k in slate.columns]
    if not keys:
        return graded
    sized = slate.drop_duplicates(subset=keys)[[*keys, "stake_sized"]]
    return graded.merge(sized, on=keys, how="left")


def prop_closing_for_slate(
    props_dir: Path, props: pd.DataFrame, games_map: pd.DataFrame, league: str
) -> pd.DataFrame | None:
    """The consensus closing prop line per (player, market, side), or ``None``.

    Reads every ``props_{league}_*.parquet`` snapshot the props collector
    banked under ``props_dir`` (twice daily; docs/PROPS.md), keeps this
    slate's games, takes each book's last pre-kickoff quote per (player,
    market, side), and reduces across books to the median ``closing_point``
    and ``closing_price``. Names match the provider's own, normalized.
    """
    from velocity.backtest.props_football import _normalize_name
    from velocity.store.pit import lines_before_kickoff

    pattern = rf"props_{re.escape(league)}_{_STAMP}\.parquet"
    snapshots = sorted(p for p in props_dir.rglob("*.parquet") if re.fullmatch(pattern, p.name))
    if not snapshots or props.empty:
        return None
    game_ids = set(games_map["game_id"].astype(str))
    frames = []
    for path in snapshots:
        snap = pd.read_parquet(path)
        if "league" in snap.columns:
            snap = snap[snap["league"].astype(str) == league]
        snap = snap[snap["game_id"].astype(str).isin(game_ids)]
        if not snap.empty:
            frames.append(snap)
    if not frames:
        return None
    # Snapshots stamp UTC-aware timestamps; a games map may carry naive
    # kickoffs (or the reverse). Put both on naive UTC before comparing.
    def _utc_naive(values: pd.Series) -> pd.Series:
        return pd.to_datetime(values, utc=True).dt.tz_localize(None)

    stacked = pd.concat(frames, ignore_index=True)
    stacked = stacked.assign(timestamp=_utc_naive(stacked["timestamp"]))
    kickoffs = games_map.assign(kickoff=_utc_naive(games_map["kickoff"]))
    lines = lines_before_kickoff(stacked, kickoffs)
    if lines.empty:
        return None
    lines = lines.assign(_player=lines["player"].astype(str).map(_normalize_name))
    lines = lines.sort_values("timestamp")
    per_book = lines.groupby(["_player", "market", "side", "book"], as_index=False).tail(1)
    consensus = (per_book.groupby(["_player", "market", "side"], as_index=False)
                 .agg(closing_point=("point", "median"), closing_price=("price", "median")))
    return consensus


def attach_prop_closes(props: pd.DataFrame, consensus: pd.DataFrame | None) -> pd.DataFrame:
    """Join the consensus closes onto the prop slate by normalized player name."""
    if consensus is None or consensus.empty or props.empty:
        return props
    from velocity.backtest.props_football import _normalize_name

    keyed = props.assign(_player=props["player"].astype(str).map(_normalize_name))
    out = keyed.drop(columns=["closing_point", "closing_price"], errors="ignore").merge(
        consensus, on=["_player", "market", "side"], how="left")
    return out.drop(columns=["_player"])


def exchange_closing_for_slate(
    exchanges_dir: Path, games_map: pd.DataFrame, league: str
) -> pd.DataFrame | None:
    """Each exchange contract's OWN close, rebuilt from the banked snapshots.

    An exchange quote cannot be compared with a sportsbook's consensus close on
    the main number — a rung is a different contract, not the same bet at a
    moved line — so it needs its own. The hourly collector banks both venues'
    raw payloads; a close is the last snapshot taken before the game started,
    re-keyed onto this slate's game ids by exactly the assembly the live board
    used (:func:`~velocity.ingest.exchanges.board_from_payloads`).

    Returns ``[game_id, market, side, book, point, price]`` — the ladder key
    :func:`velocity.report.scorecard.bets_from_slate` matches on — or ``None``
    when nothing is banked. Best-effort by design: a venue or a stamp that
    cannot be rebuilt is skipped, and the rows that do resolve still grade.
    """
    from velocity.ingest.exchanges import POLYMARKET_LEAGUE, board_from_payloads

    if games_map is None or games_map.empty or not exchanges_dir.exists():
        return None
    kickoffs = {
        str(r["game_id"]): pd.Timestamp(r["kickoff"])
        for r in games_map.to_dict("records")
        if pd.notna(r.get("kickoff"))
    }
    if not kickoffs:
        return None
    known = sorted(
        set(games_map["home_team"].astype(str)) | set(games_map["away_team"].astype(str))
    )
    gamma = POLYMARKET_LEAGUE.get(league, league)

    by_stamp: dict[str, dict[str, Any]] = {}
    for path in exchanges_dir.rglob("*.json"):
        name = path.name
        match = re.search(_STAMP + r"\.json$", name)
        if match is None:
            continue
        stamp = match.group(1)
        slot = by_stamp.setdefault(stamp, {"kalshi": {}, "events": None, "books": None})
        if name.startswith("kalshi_"):
            series = name[len("kalshi_"):-len(f"_{stamp}.json")]
            if series in kalshi_series_for(league):
                slot["kalshi"][series] = path
        elif name.startswith(f"polymarket_events_{gamma}_"):
            slot["events"] = path
        elif name.startswith(f"polymarket_books_{gamma}_"):
            slot["books"] = path
    if not by_stamp:
        return None

    frames: list[pd.DataFrame] = []
    for stamp in sorted(by_stamp):
        taken = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
        slot = by_stamp[stamp]
        try:
            payloads = {k: json.loads(v.read_text()) for k, v in slot["kalshi"].items()}
            events = json.loads(slot["events"].read_text()) if slot["events"] else None
            books = json.loads(slot["books"].read_text()) if slot["books"] else None
            if not payloads and events is None:
                continue
            board, _notes = board_from_payloads(
                league, known, games_map, pd.Timestamp(taken),
                kalshi_payloads=payloads, polymarket_events=events,
                polymarket_books=books,
            )
        except Exception as exc:  # noqa: BLE001 - one bad stamp never blocks the rest
            print(f"exchange closes: snapshot {stamp} skipped ({exc})")
            continue
        if board.empty:
            continue
        # A close is a PRE-kickoff observation. A snapshot taken after the
        # first pitch is a live price, not the number anyone could have closed
        # at, so it cannot be the benchmark.
        when = pd.Timestamp(taken)
        ahead = board["game_id"].astype(str).map(
            lambda gid, when=when: gid in kickoffs and when < kickoffs[gid]
        )
        board = board[ahead.fillna(False).astype(bool)]
        if not board.empty:
            frames.append(board.assign(_stamp=stamp))
    if not frames:
        return None

    closes = pd.concat(frames, ignore_index=True).sort_values("_stamp")
    keys = ["game_id", "market", "side", "book", "point"]
    filled = closes[keys].astype(object).where(closes[keys].notna(), "\x00")
    closes = closes[~filled.duplicated(keep="last")]
    return closes[[*keys, "price"]].reset_index(drop=True)


def kalshi_series_for(league: str) -> tuple[str, ...]:
    from velocity.ingest.exchanges import KALSHI_SERIES_BY_LEAGUE

    return tuple(KALSHI_SERIES_BY_LEAGUE.get(league, ()))


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
    # Exchange rows are a different price basis — an executable ask on one
    # rung of a ladder, not a two-way sportsbook quote on a main line — so
    # folding them into this cross-book median would silently shift the
    # sportsbook CLV benchmark and median a point across a whole ladder
    # (docs/BUILD_EXCHANGES.md E7). An exchange row is scored only against its
    # OWN contract — same venue, same strike — which the scorecard's ladder
    # close index enforces; with no exchange close banked here yet it simply
    # carries no CLV. That is the honest answer, and it replaces scoring the
    # rung against this consensus, which invented the whole distance between
    # a far-off rung and the main number as line value earned.
    from velocity.store.schema import LADDER_BOOKS

    per_book = per_book[~per_book["book"].astype(str).str.lower().isin(LADDER_BOOKS)]
    if per_book.empty:
        return None
    # Points are linear (plain median); prices are NOT — American odds are
    # discontinuous across ±100, so the price consensus is taken in decimal
    # space (the first live run crashed on a median of -2.0).
    from velocity.wagering.odds import consensus_american

    # The yardstick prefers a sharp close (docs/SYSTEM_REVIEW.md §4.3): when
    # a SHARP_BOOKS quote is on the board for the key, that is the close and
    # ``close_source`` says so; otherwise the cross-book consensus. Pinnacle
    # rides the ``eu`` region, which the collector takes as a flag — the
    # column is what lets the site say which yardstick a CLV number is.
    rows = []
    for (gid, market, side), group in per_book.groupby(
            ["game_id", "market", "side"]):
        sharp = group[group["book"].astype(str).str.lower().isin(SHARP_BOOKS)]
        chosen = sharp if not sharp.empty else group
        price = consensus_american(chosen["price"])
        if price is None:
            continue
        rows.append({"game_id": gid, "market": market, "side": side,
                     "price": price, "point": chosen["point"].median(),
                     "close_source": "sharp" if not sharp.empty else "consensus"})
    return pd.DataFrame(rows) if rows else None


def settle_ledger(  # noqa: PLR0913 - the grade's parts
    path: Path,
    league: str,
    games_graded: pd.DataFrame | None,
    props_graded: pd.DataFrame | None,
    finals: pd.DataFrame | None,
    now: datetime,
    *,
    stamp: str | None = None,
) -> None:
    """Settle the ledger's open bets from the day's grade (docs/WAGERING.md W1).

    Graded slate rows settle by bet identity — game, market, side, player —
    carrying their CLV; any open game bet left (placed off an earlier card
    the graded slate no longer lists) settles straight from the finals.
    Re-running a grade settles nothing twice. Best-effort: the ledger never
    blocks the record.
    """
    try:
        from velocity.wagering.ledger import Ledger, results_from_graded

        ledger = Ledger.load(path)
        if not ledger.seeded:
            print(f"ledger: {path} has no seed yet — nothing to settle")
            return
        before = ledger.current_bankroll()
        results = results_from_graded(league, games_graded, props_graded)
        settled = ledger.settle(results, at=now, stamp=stamp)
        from_finals = ledger.settle_from_finals(finals, at=now, league=league, stamp=stamp)
        ledger.save()
        n = len(settled) + len(from_finals)
        state = ledger.state()
        print(f"ledger: settled {n} bet(s) ({len(from_finals)} from finals); bankroll "
              f"{before:.2f} → {state.current:.2f}; {state.describe()}")
    except Exception as exc:  # noqa: BLE001 - the ledger never blocks the record
        print(f"ledger settlement skipped ({exc})")


def _newest_cumulative(prev_dir: Path, league: str) -> pd.DataFrame | None:
    """The season record chain that reaches furthest, from every copy on hand.

    Copies come from two places: the previous runs' artifacts (60-day
    retention) and the durable copy the workflow parks in R2 after each
    grading (docs/STRATEGY_REVIEW.md S1) — a workflow-failure streak or a
    retention gap used to lose the season. The chain to carry forward is the
    one whose newest settled day is latest, ties to the longer one; the
    filename stamp says when a copy was *written*, which is not the same
    thing once a copy has been round-tripped through the store.
    """
    pattern = rf"cumulative_record_{re.escape(league)}_{_STAMP}\.parquet"
    matches = sorted(
        (p for p in prev_dir.rglob("*.parquet") if re.fullmatch(pattern, p.name)),
        key=lambda p: p.name,
    )
    return newest_chain(_read_chains(matches))


def _read_chains(paths: list[Path]) -> list[pd.DataFrame]:
    """Every readable copy, skipping the ones that are not parquet at all.

    A failed R2 fetch leaves a **zero-byte** file where the chain should be
    (wrangler creates ``--file`` before it discovers the object is missing),
    and pyarrow raises on it. That raise used to kill the grader between
    writing the day's record and writing the season chain, so the chain was
    never written, never parked, and never found on the next run — a
    bootstrap deadlock that silently cost the season (the whole Performance
    page read empty). An unreadable copy is now reported and skipped.
    """
    chains: list[pd.DataFrame] = []
    for path in paths:
        if path.stat().st_size == 0:
            print(f"chain copy {path.name} is empty — skipped")
            continue
        try:
            chains.append(pd.read_parquet(path))
        except Exception as exc:  # noqa: BLE001 - a bad copy never blocks the chain
            print(f"chain copy {path.name} unreadable ({exc}) — skipped")
    return chains


def _bootstrap_chain(prev_dir: Path, league: str) -> pd.DataFrame | None:
    """Start the season chain from the daily records already banked.

    With no chain copy anywhere — a first run, or the seasons the zero-byte
    bug above silently ate — the history is not actually lost: every previous
    run's artifact still carries its ``record_{league}_{stamp}.parquet``.
    Folding those settled rows in recovers the record instead of restarting
    it at zero, and the dedup key in ``accumulate_record`` makes replaying a
    day idempotent.
    """
    from velocity.report.daily_record import accumulate_record

    pattern = rf"record_{re.escape(league)}_{_STAMP}\.parquet"
    matches = sorted(
        (p for p in prev_dir.rglob("*.parquet") if re.fullmatch(pattern, p.name)),
        key=lambda p: p.name,
    )
    days = [f for f in _read_chains(matches) if not f.empty and "result" in f.columns]
    if not days:
        return None
    chain = None
    for day in days:
        chain = accumulate_record(chain, day[day["result"] != "pending"])
    if chain is None or chain.empty:
        return None
    print(f"season chain bootstrapped from {len(days)} banked daily record(s): "
          f"{len(chain)} settled row(s)")
    return chain


def newest_chain(chains: list[pd.DataFrame]) -> pd.DataFrame | None:
    """The chain reaching the latest settled day (longest on a tie), or None.

    ``chains`` arrive oldest-stamp first; a set of copies none of which
    carries a settled date (a chain from before dates rode along) falls back
    to the newest-written copy, which is all the stamp can say.
    """
    best: pd.DataFrame | None = None
    best_key: tuple[pd.Timestamp, int] | None = None
    fallback: pd.DataFrame | None = None
    for chain in chains:
        if chain is None or chain.empty:
            continue
        fallback = chain
        if "slate_date" not in chain.columns:
            continue
        latest = pd.to_datetime(chain["slate_date"], errors="coerce").max()
        if pd.isna(latest):
            continue
        key = (latest, len(chain))
        if best_key is None or key > best_key:
            best, best_key = chain, key
    return best if best is not None else fallback


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
    parser.add_argument("--props-dir", default="artifacts/props",
                        help="the props collector's banked snapshots (prop closes)")
    parser.add_argument("--odds-dir", default="artifacts/odds",
                        help="downloaded odds-lines snapshots (the CLV close source)")
    parser.add_argument("--exchanges-dir", default="artifacts/exchanges",
                        help="downloaded Kalshi/Polymarket snapshots — where an exchange "
                             "contract's OWN close comes from (a ladder rung cannot be "
                             "graded against the sportsbook's main-line consensus)")
    parser.add_argument("--ledger", default=None,
                        help="bankroll ledger parquet: settle its open bets from the grade "
                             "(docs/WAGERING.md W1)")
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
    graded_stamps = prior_stamps(stamps, now)
    if not graded_stamps:
        print("no prior-day slate found in the downloaded artifacts; skipping record")
        return
    stamp = graded_stamps[-1]

    def _merged(kind: str) -> pd.DataFrame | None:
        return merge_stamped(
            [f for s in graded_stamps if (f := _load(stamps[s], kind)) is not None], kind
        )

    slate = _merged("slate")
    props = _merged("props")
    parlays = _merged("parlays")
    games_map = _merged("games")
    portfolio = _merged("portfolio")
    slate = attach_sized_stakes(slate, portfolio, "game")
    props = attach_sized_stakes(props, portfolio, "prop")
    n_plays = sum(0 if f is None else len(f) for f in (slate, props, parlays))
    which = stamp if len(graded_stamps) == 1 else f"{len(graded_stamps)} runs → {stamp}"
    print(f"grading slate {which}: {n_plays} play(s)")

    slate_date = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
    record = None
    finals = None
    games_graded = props_graded = None
    venue_closes = None
    closing_book = None
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
            finals = finals_for_slate(
                games_map, schedule, aliases=aliases,
                schedule_tz=SCHEDULE_TZ_BY_LEAGUE.get(args.league),
            )
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
            # Exchange rows need their own contract's close; the sportsbook
            # consensus above deliberately excludes the venues, so without this
            # every ladder rung grades with no CLV at all.
            venue_closes = None
            if slate is not None and not slate.empty:
                try:
                    venue_closes = exchange_closing_for_slate(
                        Path(args.exchanges_dir), games_map, args.league)
                    matched = 0 if venue_closes is None else len(venue_closes)
                    print(f"exchange closes: {matched} contract(s) matched")
                except Exception as exc:  # noqa: BLE001 - CLV never blocks grading
                    print(f"exchange closes skipped ({exc})")
            closing_book = closing
            if venue_closes is not None and not venue_closes.empty:
                closing = (venue_closes if closing is None else
                           pd.concat([closing, venue_closes], ignore_index=True))
            games_graded = (None if slate is None or slate.empty
                            else grade_slate(slate, finals, closing))
            games_graded = sized_profit(_carry_sized(games_graded, slate))
            games_graded = attach_close_source(games_graded, closing)
            # Prop closes from the props collector's archive — attached
            # before grading so the ledger carries prop CLV (docs/PROPS.md).
            if props is not None and not props.empty:
                try:
                    consensus = prop_closing_for_slate(
                        Path(args.props_dir), props, games_map, args.league)
                    props = attach_prop_closes(props, consensus)
                    matched = 0 if consensus is None else int(props["closing_point"].notna().sum())
                    print(f"prop closing lines: {matched} slate rows matched a close")
                except Exception as exc:  # noqa: BLE001 - CLV never blocks grading
                    print(f"prop closing lines skipped ({exc})")
            props_graded = sized_profit(
                _carry_sized(_grade_props(args.league, props, schedule, slate_date), props)
            )
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
    # can't settle today is never revisited (every prior-day run is graded, but
    # only the prior day), so a pending row would be permanent noise — the first live
    # run proved it by folding 715 stale future-game bets into the season line.
    # Filtering the downloaded chain too heals any history that already
    # carries them.
    prior = _newest_cumulative(Path(args.prev_dir), args.league)
    if prior is None:
        prior = _bootstrap_chain(Path(args.prev_dir), args.league)
    if prior is not None and "result" in prior.columns:
        prior = prior[prior["result"] != "pending"]
    settled_record = record[record["result"] != "pending"] if not record.empty else record
    cumulative = accumulate_record(prior, settled_record)
    cumulative.to_parquet(
        out / f"cumulative_record_{args.league}_{out_stamp}.parquet", index=False
    )
    print(f"season record: {len(cumulative)} settled row(s) accumulated")

    # Cross-venue sharpness (docs/BUILD_EXCHANGES.md E7) — the question the
    # exchange build exists to answer: does a venue close sharper than the
    # sportsbook consensus, or softer? Softer is where an origination edge can
    # survive; sharper makes it a benchmark rather than a bet. Needs both
    # closing frames and the day's finals, so it rides the grade. Best-effort
    # like every surface after the record itself.
    if venue_closes is not None and not venue_closes.empty and finals is not None:
        try:
            from velocity.eval.venues import compare_closes, fair_closing_probabilities

            book_fair = fair_closing_probabilities(
                closing_book if closing_book is not None else pd.DataFrame()
            )
            venue_fair = fair_closing_probabilities(venue_closes)
            gaps = compare_closes(book_fair, venue_fair)
            if not gaps.empty:
                gaps.assign(league=args.league, as_of=pd.Timestamp(slate_date)).to_parquet(
                    out / f"venues_{args.league}_{out_stamp}.parquet", index=False
                )
                mean_gap = float(gaps["disagreement"].mean())
                print(f"cross-venue: {len(gaps)} contract(s) quoted at both; "
                      f"the exchange closed the side {mean_gap:+.4f} vs the book")
            else:
                print("cross-venue: no contract closed at both a book and a venue")
        except Exception as exc:  # noqa: BLE001 - a report never blocks the record
            print(f"cross-venue report skipped ({exc})")

    # The market monitor (docs/WAGERING.md W3): per-market trailing CLV and
    # ROI over 7/30 days with flags, read off the chain just written. One
    # parquet per grade for the site's health page, and the table in the log.
    try:
        from velocity.report.monitor import health_lines, market_health

        health = market_health(cumulative, as_of=slate_date)
        health.assign(league=args.league, as_of=pd.Timestamp(slate_date)).to_parquet(
            out / f"monitor_{args.league}_{out_stamp}.parquet", index=False
        )
        print("\n".join(health_lines(health, args.league)))
    except Exception as exc:  # noqa: BLE001 - the monitor never blocks the record
        print(f"market monitor skipped ({exc})")

    # The bankroll ledger: every placed bet the grade can settle, settled —
    # at the placed stakes and prices, with the close it was graded against.
    # Open game bets the slate no longer carries settle from the finals.
    if args.ledger:
        settle_ledger(Path(args.ledger), args.league, games_graded, props_graded, finals,
                      now, stamp=stamp)

    # Post-game graphics — the Sim Check cards (actual result on the pregame
    # distribution) and the model record card. Best-effort: rendering trouble
    # never blocks the graded record itself.
    try:
        from velocity.report.sim_check import build_sim_checks
        from velocity.report.social_png import render_record_card, render_sim_checks

        projections_frame = _merged("projections")
        distributions = _merged("distributions")
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
