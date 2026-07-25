"""Walk-forward MLB backtest — price each day's entry board, grade on CLV.

The driver that turns the banked odds archive into the acceptance report for #23.
It is the MLB counterpart of :func:`velocity.backtest.engine.walk_forward`, but the
point-in-time anchor is the **game date**, not a week, and the "model fit" is a
fresh season-to-date build (:func:`velocity.models.mlb_build.build_mlb_asof`) rather
than a play-level regression.

The flow, per game-day:

1. :func:`velocity.backtest.archive.select_boards` has already split the multi-snapshot
   archive into an **entry** board (our bet time) and a **closing** board (the CLV
   anchor) per game.
2. The as-of model for that date projects each game (teams resolved exactly as the
   live slate resolves them).
3. The entry board is priced through the standard wagering path
   (:func:`velocity.wagering.live.build_live_slate`) — de-vig, edge, Kelly — so the
   backtest bets are the same bets the live runner would place.
4. Each staked bet is graded against the **closing** board (CLV) and the game's
   **final** (win/loss), reusing :func:`velocity.report.scorecard.grade_slate`.

The accumulated ledger is scored by the same league-agnostic scorecard as the live
slate, so the season summary is CLV-by-market + a calibration table, not a P&L
curve. Everything except the per-date model build is pure and offline-tested: the
model is injected as a ``model_factory`` so a stub prices a tiny fixture archive
with no network.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import pandas as pd

from velocity.backtest.archive import select_boards
from velocity.models.game_nfl import GameProjection
from velocity.report.scorecard import (
    calibration_table,
    clv_by_market,
    grade_slate,
    summarize,
)
from velocity.wagering.live import (
    MLB_TEAM_ALIASES,
    build_live_slate,
    canonicalize_sides,
    slate_to_frame,
)
from velocity.wagering.slate import SlateConfig

# A day's model: given the game date, hand back a projector + its team universe.
ModelFactory = Callable[[str], tuple[Callable[[str, str], GameProjection], Iterable[str]]]

_LEDGER_COLUMNS = [
    "date", "game_id", "market", "side", "book", "price", "point", "stake",
    "p_model", "result", "profit", "bankroll", "price_clv", "line_clv",
]


def _empty_ledger() -> pd.DataFrame:
    return pd.DataFrame(columns=_LEDGER_COLUMNS)


def _game_dates(kickoff: pd.Series) -> pd.Series:
    """Kickoffs → the official MLB game date (US Eastern), as ISO strings.

    The archive's ``kickoff`` is naive UTC; a night game there spills into the next
    UTC day, so grouping on the UTC date would mis-date it (and, worse, leak same-day
    results into the as-of window). US-Eastern calendar date is the standard baseball
    date and keeps every night game on its real day.
    """
    ts = pd.to_datetime(kickoff, errors="coerce", utc=True)
    eastern = ts.dt.tz_convert("America/New_York")
    return eastern.dt.date.astype(str)


def backtest_day(
    entry: pd.DataFrame,
    closing: pd.DataFrame,
    events: pd.DataFrame,
    finals: pd.DataFrame,
    project: Callable[[str, str], GameProjection],
    known_teams: Iterable[str],
    *,
    config: SlateConfig | None = None,
    aliases: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Price one day's entry board and grade it against the closing board + finals.

    Pure: ``project`` and ``known_teams`` come from the day's model, ``finals`` is a
    ``Games``-shaped frame keyed by the archive's ``game_id`` (see
    :func:`velocity.report.results.finals_for_slate`). Returns the graded bet rows
    (with both CLV measures); empty if nothing qualified.
    """
    # exclude_closing=False: the entry board is a single snapshot and IS the board we
    # price; CLV is measured separately against the real closing board below.
    cfg = config or SlateConfig(exclude_closing=False)
    alias_map = MLB_TEAM_ALIASES if aliases is None else aliases
    log, _ = build_live_slate(events, entry, project, known_teams, cfg, alias_map)
    slate = slate_to_frame(log)
    if slate.empty:
        return slate.iloc[:0]
    closing_canon = canonicalize_sides(closing, events)
    return grade_slate(slate, finals, closing_canon)


@dataclass(frozen=True)
class BacktestReport:
    """The season acceptance report: the graded ledger plus its scorecard views."""

    ledger: pd.DataFrame  # one row per staked bet, graded (CLV + win/loss)
    summary: dict[str, float]  # record, ROI, mean CLV, ECE
    clv_by_market: pd.DataFrame  # mean price/line CLV + % positive, per market
    calibration: pd.DataFrame  # realized win rate vs model probability, per bucket


def run_archive_backtest(
    lines: pd.DataFrame,
    events: pd.DataFrame,
    finals: pd.DataFrame,
    model_factory: ModelFactory,
    *,
    config: SlateConfig | None = None,
    aliases: dict[str, str] | None = None,
) -> BacktestReport:
    """Walk the banked archive day by day and score the accumulated bets.

    ``lines`` / ``events`` are the concatenated banked archive (multi-snapshot);
    ``finals`` maps the archive's ``game_id`` to a played final. ``model_factory(date)``
    returns that date's ``(project, known_teams)`` — the as-of build in production, a
    stub in tests. The per-game entry/closing split (:func:`select_boards`) is done
    once up front; each game-day is then priced with its own point-in-time model.
    """
    boards = select_boards(lines, events)
    if boards.events.empty:
        ledger = _empty_ledger()
        return BacktestReport(ledger, summarize(ledger), clv_by_market(ledger),
                              calibration_table(ledger))

    ev = boards.events.copy()
    ev["date"] = _game_dates(ev["kickoff"])
    entry_gid = boards.entry["game_id"].astype(str)
    closing_gid = boards.closing["game_id"].astype(str)
    finals_gid = finals["game_id"].astype(str)

    parts: list[pd.DataFrame] = []
    for date, day_events in ev.groupby("date"):
        gids = set(day_events["game_id"].astype(str))
        entry = boards.entry[entry_gid.isin(gids)]
        closing = boards.closing[closing_gid.isin(gids)]
        day_finals = finals[finals_gid.isin(gids)]
        project, known = model_factory(str(date))
        graded = backtest_day(
            entry, closing, day_events, day_finals, project, known,
            config=config, aliases=aliases,
        )
        if not graded.empty:
            graded = graded.copy()
            graded.insert(0, "date", str(date))
            parts.append(graded)

    ledger = pd.concat(parts, ignore_index=True) if parts else _empty_ledger()
    return BacktestReport(
        ledger=ledger,
        summary=summarize(ledger),
        clv_by_market=clv_by_market(ledger),
        calibration=calibration_table(ledger),
    )


def walk_forward_mlb(
    lines: pd.DataFrame,
    events: pd.DataFrame,
    season: int,
    *,
    config: SlateConfig | None = None,
    seed: int = 0,
) -> BacktestReport:  # pragma: no cover - network
    """Network entry point: build each day's as-of model + finals, then backtest.

    Resolves finals once from the StatsAPI schedule spanning the archive, and wires
    :func:`build_mlb_asof` as the per-date ``model_factory``. Offline logic lives in
    :func:`run_archive_backtest`; this only supplies the two networked inputs.
    """
    from velocity.ingest.mlb import load_schedule
    from velocity.models.mlb_build import build_mlb_asof
    from velocity.report.results import finals_for_slate

    games_map = events[["game_id", "home_team", "away_team", "kickoff"]].copy()
    dates = _game_dates(events["kickoff"])
    schedule = load_schedule(dates.min(), dates.max())
    finals = finals_for_slate(games_map, schedule)

    def factory(date: str) -> tuple[Callable[[str, str], GameProjection], Iterable[str]]:
        model, _ = build_mlb_asof(date, season, seed=seed)
        return model.project_full, model.known_teams

    return run_archive_backtest(lines, events, finals, factory, config=config)
