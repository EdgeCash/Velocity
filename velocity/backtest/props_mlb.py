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
from velocity.report.scorecard import clv_by_market
from velocity.wagering.bet_log import BetLog
from velocity.wagering.live import MLB_TEAM_ALIASES
from velocity.wagering.props_slate import mlb_prop_slate
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


@dataclass(frozen=True)
class PropBacktestReport:
    """The prop CLV acceptance report: the per-bet ledger plus its CLV views."""

    ledger: pd.DataFrame  # one row per staked prop bet, with both CLV measures
    summary: dict[str, float]  # count, mean price/line CLV, % beating the close
    clv_by_market: pd.DataFrame  # per prop market: mean CLV + % positive


def backtest_prop_day(
    entry: pd.DataFrame,
    closing: pd.DataFrame,
    events: pd.DataFrame,
    model: MLBGameModel,
    name_to_id: Mapping[str, str],
    *,
    config: SlateConfig | None = None,
    aliases: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Price one day's entry prop board with ``model`` and attach the closing board.

    Pure: ``model`` + ``name_to_id`` come from the day's point-in-time build. Returns
    the CLV ledger (empty if nothing was staked).
    """
    cfg = config or SlateConfig(exclude_closing=False)
    log, _ = mlb_prop_slate(
        model, events, entry, name_to_id,
        aliases=aliases or MLB_TEAM_ALIASES, config=cfg,
    )
    return prop_clv_ledger(log, closing)


def run_prop_archive_backtest(
    lines: pd.DataFrame,
    events: pd.DataFrame,
    model_factory: PropModelFactory,
    *,
    config: SlateConfig | None = None,
    aliases: Mapping[str, str] | None = None,
) -> PropBacktestReport:
    """Walk the banked prop archive day by day and score the staked bets on CLV.

    ``lines`` is the concatenated banked ``PropLines`` **with a ``snapshot`` column**
    (the pull time); ``events`` carries each game's ``kickoff`` + teams.
    ``model_factory(date)`` returns that date's ``(model, name_to_id)`` — the as-of
    build in production, a stub in tests.
    """
    boards = select_boards(lines, events)
    if boards.events.empty:
        empty = _empty_ledger()
        return PropBacktestReport(empty, _summarize_clv(empty), clv_by_market(empty))

    ev = boards.events.copy()
    ev["date"] = _game_dates(ev["kickoff"])
    entry_gid = boards.entry["game_id"].astype(str)
    closing_gid = boards.closing["game_id"].astype(str)

    parts: list[pd.DataFrame] = []
    for date, day_events in ev.groupby("date"):
        gids = set(day_events["game_id"].astype(str))
        entry = boards.entry[entry_gid.isin(gids)]
        closing = boards.closing[closing_gid.isin(gids)]
        model, name_to_id = model_factory(str(date))
        led = backtest_prop_day(
            entry, closing, day_events, model, name_to_id, config=config, aliases=aliases,
        )
        if not led.empty:
            led = led.copy()
            led.insert(0, "date", str(date))
            parts.append(led)

    ledger = pd.concat(parts, ignore_index=True) if parts else _empty_ledger()
    return PropBacktestReport(ledger, _summarize_clv(ledger), clv_by_market(ledger))


def walk_forward_props_mlb(
    lines: pd.DataFrame,
    events: pd.DataFrame,
    season: int,
    *,
    config: SlateConfig | None = None,
    seed: int = 0,
    n_sims: int = 2_000,
    cache_dir: str | None = None,
) -> PropBacktestReport:  # pragma: no cover - network
    """Network entry point: build each day's as-of model, then score prop CLV.

    ``build_mlb_asof`` already returns ``(model, name_index)``, so it *is* the prop
    ``model_factory``. Offline logic lives in :func:`run_prop_archive_backtest`.
    """
    from velocity.models.mlb_build import build_mlb_asof

    def factory(date: str) -> tuple[MLBGameModel, Mapping[str, str]]:
        model, names = build_mlb_asof(date, season, seed=seed, n_sims=n_sims, cache_dir=cache_dir)
        return model, names

    return run_prop_archive_backtest(
        lines, events, factory, config=config, aliases=MLB_TEAM_ALIASES
    )
