"""Walk-forward MLB **prop CLV** backtest — grade-free (closing-line value only).

Prop CLV needs no box scores: :meth:`Bet.line_clv` / :meth:`Bet.price_clv` measure
the entry price/number against the close, and CLV — a market-relative signal — is
the durable read on edge (per #23). So this replays the banked prop archive day by
day, prices each day's **entry** prop board with the point-in-time model
(:func:`build_mlb_asof`), attaches the **closing** prop board, and reports CLV by
prop market. Win/loss grading (which *does* need player box scores) is a separate
layer (P2 of the props/derivatives backtest).

The scaffolding is shared with the game-market backtest: :func:`select_boards` splits
the multi-snapshot archive into entry/closing boards (it is market-agnostic), and the
day-grouping + as-of model loop mirrors :func:`velocity.backtest.mlb.run_archive_backtest`.
Only the pricing (``mlb_prop_slate`` instead of ``build_live_slate``) and the
CLV-only ledger differ. Everything except the network model build is offline-tested.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from velocity.backtest.archive import select_boards
from velocity.backtest.mlb import _game_dates
from velocity.models.game_mlb import MLBGameModel
from velocity.report.scorecard import calibration_table, clv_by_market, summarize
from velocity.wagering.bet_log import BetLog
from velocity.wagering.live import MLB_TEAM_ALIASES
from velocity.wagering.odds import net_payout
from velocity.wagering.props_slate import mlb_prop_slate, resolve_player
from velocity.wagering.slate import SlateConfig

# A day's prop model: given the game date, hand back the point-in-time MLB model and
# the player name→id index (both come straight from ``build_mlb_asof``).
PropModelFactory = Callable[[str], tuple[MLBGameModel, Mapping[str, str]]]

_LEDGER_COLUMNS = [
    "date", "game_id", "market", "player", "side", "point", "price",
    "stake", "p_model", "price_clv", "line_clv",
]


def _empty_ledger() -> pd.DataFrame:
    return pd.DataFrame(columns=_LEDGER_COLUMNS)


def _prop_closing_index(
    closing: pd.DataFrame,
) -> dict[tuple[str, str, str, str], tuple[Any, float | None]]:
    """Map ``(game_id, market, player, side)`` → ``(closing_price, closing_point)``.

    Props key on the player too (a game has many), and both boards carry the same
    Odds-API player name, so no side canonicalization is needed (unlike game markets).
    """
    index: dict[tuple[str, str, str, str], tuple[Any, float | None]] = {}
    if closing.empty:
        return index
    for row in closing.to_dict("records"):
        key = (str(row["game_id"]), str(row["market"]), str(row.get("player")), str(row["side"]))
        point = row.get("point")
        index[key] = (row.get("price"), None if point is None or pd.isna(point) else float(point))
    return index


def prop_clv_ledger(log: BetLog, closing: pd.DataFrame) -> pd.DataFrame:
    """Attach the closing prop board to a staked prop :class:`BetLog` → per-bet CLV.

    Each bet is matched to its closing price/number by ``(game_id, market, player,
    side)``; a bet with no matching close simply gets ``None`` CLV. Returns one row
    per bet with both CLV measures (``price_clv`` for the juice, ``line_clv`` for the
    number).
    """
    close = _prop_closing_index(closing)
    rows: list[dict[str, object]] = []
    for bet in log:
        c_price, c_point = close.get(
            (bet.game_id, bet.market, str(bet.player), bet.side), (None, None)
        )
        priced = replace(
            bet,
            closing_price=None if c_price is None or pd.isna(c_price) else float(c_price),
            closing_point=c_point,
        )
        rows.append({
            "game_id": priced.game_id, "market": priced.market, "player": priced.player,
            "side": priced.side, "point": priced.point, "price": priced.price,
            "stake": priced.stake, "p_model": priced.p_model,
            "price_clv": priced.price_clv(), "line_clv": priced.line_clv(),
        })
    cols = [c for c in _LEDGER_COLUMNS if c != "date"]
    return pd.DataFrame(rows, columns=cols)


def _grade_over_under(side: str, actual: float, point: float) -> str:
    """Grade one over/under prop against the player's actual stat (pushes on a tie)."""
    if actual == point:
        return "push"
    higher = actual > point
    return "win" if (higher == (side == "over")) else "loss"


def grade_prop_ledger(
    ledger: pd.DataFrame,
    boxscores: pd.DataFrame,
    name_to_id: Mapping[str, str],
) -> pd.DataFrame:
    """Grade each prop bet against the player's actual box-score stat → result + profit.

    ``boxscores`` is a per-player actual-stat frame (``normalize_boxscore``:
    ``game_id, player_id, <stat cols>``). A bet's player name is resolved to an id
    (``name_to_id``), then the actual for its market is compared to the point. A bet
    whose player/stat isn't in the box score (didn't play, unresolved) is ``pending``.
    The result is scorecard-shaped — ``result`` + ``profit`` alongside the CLV columns
    — so :func:`scorecard.summarize` / :func:`calibration_table` score it directly.
    """
    actuals: dict[tuple[str, str], dict] = {
        (str(r["game_id"]), str(r["player_id"])): r for r in boxscores.to_dict("records")
    } if not boxscores.empty else {}
    results: list[str] = []
    profits: list[float] = []
    for row in ledger.to_dict("records"):
        pid = resolve_player(str(row["player"]), name_to_id)
        stats = actuals.get((str(row["game_id"]), pid)) if pid is not None else None
        actual = stats.get(row["market"]) if stats is not None else None
        if actual is None or pd.isna(actual) or row["point"] is None or pd.isna(row["point"]):
            results.append("pending")
            profits.append(float("nan"))
            continue
        result = _grade_over_under(str(row["side"]), float(actual), float(row["point"]))
        stake = float(row["stake"])
        profit = (
            stake * net_payout(float(row["price"])) if result == "win"
            else -stake if result == "loss" else 0.0
        )
        results.append(result)
        profits.append(profit)
    graded = ledger.copy()
    graded["result"] = results
    graded["profit"] = profits
    return graded


def _summarize_clv(ledger: pd.DataFrame) -> dict[str, float]:
    """One-line prop CLV summary: count, mean price/line CLV, % beating the close."""
    if ledger.empty:
        return {"n_bets": 0, "mean_price_clv": float("nan"),
                "mean_line_clv": float("nan"), "pct_positive_clv": float("nan")}
    price = ledger["price_clv"].dropna()
    line = ledger["line_clv"].dropna()
    beat = ledger["price_clv"].where(ledger["price_clv"].notna(), ledger["line_clv"]).dropna()
    return {
        "n_bets": int(len(ledger)),
        "mean_price_clv": round(float(price.mean()), 4) if not price.empty else float("nan"),
        "mean_line_clv": round(float(line.mean()), 3) if not line.empty else float("nan"),
        "pct_positive_clv": round(float((beat > 0).mean()), 3) if not beat.empty else float("nan"),
    }


def _prop_report(ledger: pd.DataFrame) -> PropBacktestReport:
    """Build the report — a full scorecard when graded, CLV-only otherwise."""
    graded = "result" in ledger.columns
    summary = summarize(ledger) if graded else _summarize_clv(ledger)
    calibration = calibration_table(ledger) if graded else pd.DataFrame()
    return PropBacktestReport(ledger, summary, clv_by_market(ledger), calibration)


@dataclass(frozen=True)
class PropBacktestReport:
    """The prop acceptance report: the per-bet ledger plus its CLV (and, if graded,
    win/loss + calibration) views."""

    ledger: pd.DataFrame  # one row per staked prop bet; CLV always, result/profit if graded
    summary: dict[str, float]  # CLV-only, or the full scorecard (ROI, record, ECE) if graded
    clv_by_market: pd.DataFrame  # per prop market: mean CLV + % positive
    calibration: pd.DataFrame  # realized vs model p per bucket (empty unless graded)


def backtest_prop_day(
    entry: pd.DataFrame,
    closing: pd.DataFrame,
    events: pd.DataFrame,
    model: MLBGameModel,
    name_to_id: Mapping[str, str],
    *,
    boxscores: pd.DataFrame | None = None,
    config: SlateConfig | None = None,
    aliases: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Price one day's entry prop board, attach the closing board, optionally grade.

    Pure: ``model`` + ``name_to_id`` come from the day's point-in-time build. With
    ``boxscores`` (that day's per-player actual stats) the CLV ledger is also graded
    to win/loss + profit; without it, CLV only. Empty if nothing was staked.
    """
    cfg = config or SlateConfig(exclude_closing=False)
    log, _ = mlb_prop_slate(
        model, events, entry, name_to_id,
        aliases=aliases or MLB_TEAM_ALIASES, config=cfg,
    )
    led = prop_clv_ledger(log, closing)
    if boxscores is not None and not led.empty:
        led = grade_prop_ledger(led, boxscores, name_to_id)
    return led


def run_prop_archive_backtest(
    lines: pd.DataFrame,
    events: pd.DataFrame,
    model_factory: PropModelFactory,
    *,
    boxscores: pd.DataFrame | None = None,
    config: SlateConfig | None = None,
    aliases: Mapping[str, str] | None = None,
) -> PropBacktestReport:
    """Walk the banked prop archive day by day and score the staked bets.

    ``lines`` is the concatenated banked ``PropLines`` **with a ``snapshot`` column**
    (the pull time); ``events`` carries each game's ``kickoff`` + teams.
    ``model_factory(date)`` returns that date's ``(model, name_to_id)`` — the as-of
    build in production, a stub in tests. Pass ``boxscores`` (a ``normalize_boxscore``
    frame spanning the archive) to grade win/loss + ROI + calibration on top of CLV;
    omit it for the grade-free CLV read.
    """
    boards = select_boards(lines, events)
    if boards.events.empty:
        return _prop_report(_empty_ledger())

    ev = boards.events.copy()
    ev["date"] = _game_dates(ev["kickoff"])
    entry_gid = boards.entry["game_id"].astype(str)
    closing_gid = boards.closing["game_id"].astype(str)

    parts: list[pd.DataFrame] = []
    for date, day_events in ev.groupby("date"):
        gids = set(day_events["game_id"].astype(str))
        entry = boards.entry[entry_gid.isin(gids)]
        closing = boards.closing[closing_gid.isin(gids)]
        day_box = None
        if boxscores is not None:
            day_box = boxscores[boxscores["game_id"].astype(str).isin(gids)]
        model, name_to_id = model_factory(str(date))
        led = backtest_prop_day(
            entry, closing, day_events, model, name_to_id,
            boxscores=day_box, config=config, aliases=aliases,
        )
        if not led.empty:
            led = led.copy()
            led.insert(0, "date", str(date))
            parts.append(led)

    ledger = pd.concat(parts, ignore_index=True) if parts else _empty_ledger()
    return _prop_report(ledger)


def load_archive_boxscores(
    events: pd.DataFrame,
    *,
    aliases: Mapping[str, str] | None = None,
) -> pd.DataFrame:  # pragma: no cover - network
    """Fetch per-player box scores for every game in the archive, keyed by ``game_id``.

    The grading source for player props. It spans the archive's date range with one
    StatsAPI schedule pull, resolves each Odds-API ``game_id`` to its ``gamePk``
    (:func:`gamepks_for_slate`), fetches each game's box score, and tags every row
    with the Odds-API ``game_id`` so :func:`grade_prop_ledger` joins straight onto the
    ledger. A game that fails to resolve or fetch is skipped (its props stay pending).
    """
    from velocity.ingest.mlb import _empty_boxscore, load_boxscore, load_schedule
    from velocity.report.results import gamepks_for_slate

    kick = pd.to_datetime(events["kickoff"], errors="coerce", utc=True).dropna()
    if kick.empty:
        return _empty_boxscore()
    schedule = load_schedule(kick.min().date().isoformat(), kick.max().date().isoformat())
    pks = gamepks_for_slate(events, schedule, aliases=aliases or MLB_TEAM_ALIASES)

    frames: list[pd.DataFrame] = []
    for row in pks.to_dict("records"):
        try:
            frames.append(load_boxscore(str(row["game_pk"]), game_id=str(row["game_id"])))
        except Exception as exc:  # noqa: BLE001 - one missing box score shouldn't stop grading
            print(f"boxscore {row['game_pk']} ({row['game_id']}) failed ({exc})")
    return pd.concat(frames, ignore_index=True) if frames else _empty_boxscore()


def walk_forward_props_mlb(
    lines: pd.DataFrame,
    events: pd.DataFrame,
    season: int,
    *,
    boxscores: pd.DataFrame | None = None,
    config: SlateConfig | None = None,
    seed: int = 0,
    n_sims: int = 2_000,
    cache_dir: str | None = None,
) -> PropBacktestReport:  # pragma: no cover - network
    """Network entry point: build each day's as-of model, then score props.

    ``build_mlb_asof`` already returns ``(model, name_index)``, so it *is* the prop
    ``model_factory``. Pass ``boxscores`` (a ``normalize_boxscore`` frame over the
    archive's games) to grade win/loss + ROI + calibration; omit for CLV only. Offline
    logic lives in :func:`run_prop_archive_backtest`.
    """
    from velocity.models.mlb_build import build_mlb_asof

    def factory(date: str) -> tuple[MLBGameModel, Mapping[str, str]]:
        model, names = build_mlb_asof(date, season, seed=seed, n_sims=n_sims, cache_dir=cache_dir)
        return model, names

    return run_prop_archive_backtest(
        lines, events, factory, boxscores=boxscores, config=config, aliases=MLB_TEAM_ALIASES
    )
