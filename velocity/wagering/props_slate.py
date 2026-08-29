"""Player-prop slate — model prop distributions + prop lines → staked bets.

The game-market analogue of :mod:`velocity.wagering.slate`, for player props. For
each game it takes the model's per-player prop distributions (any object
satisfying :class:`PropDistributions` — empirical distributions read off the
correlated sim) and the provider's prop board, resolves each provider player
name to a model player id, de-vigs each over/under pair, measures edge, and
stakes survivors with the same fractional-Kelly-plus-group-cap discipline.
Unresolved players are skipped and reported, never guessed.

CLV is not measured here — a live snapshot is the only board — so bets are logged
without a close; the prop-line archive (the collector) is what a later backtest
grades them against.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Protocol

import pandas as pd

from velocity.wagering.bet_log import Bet, BetLog
from velocity.wagering.devig import devig
from velocity.wagering.edge import evaluate
from velocity.wagering.slate import SlateConfig
from velocity.wagering.staking import apply_group_cap, stake_amount

_OPPOSITE = {"over": "under", "under": "over"}


class PropDistributions(Protocol):
    """Per-player prop distributions a game's simulation exposes for pricing."""

    def has(self, player_id: str, market: str) -> bool: ...

    def prob_over(self, player_id: str, market: str, point: float) -> float: ...

    def prob_under(self, player_id: str, market: str, point: float) -> float: ...


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def build_name_index(*stats_frames: pd.DataFrame) -> dict[str, str]:
    """Map normalized player name → player id from ``(player_id, player_name)`` frames."""
    index: dict[str, str] = {}
    for frame in stats_frames:
        for pid, name in zip(frame["player_id"], frame["player_name"], strict=False):
            index[_normalize(str(name))] = str(pid)
    return index


def resolve_player(name: str, name_to_id: Mapping[str, str]) -> str | None:
    """Provider player name → model player id (normalized match), or ``None``."""
    return name_to_id.get(_normalize(name))


def build_prop_slate(
    props_by_game: Mapping[str, PropDistributions],
    prop_lines: pd.DataFrame,
    name_to_id: Mapping[str, str],
    config: SlateConfig | None = None,
) -> tuple[BetLog, list[dict[str, str]]]:
    """Build a :class:`BetLog` of staked prop bets, plus the unresolved players."""
    config = config or SlateConfig(exclude_closing=False)
    log = BetLog()
    unresolved: list[dict[str, str]] = []

    for game_id, props in props_by_game.items():
        game_lines = prop_lines[prop_lines["game_id"].astype(str) == str(game_id)]
        if game_lines.empty:
            continue
        records = game_lines.to_dict("records")

        # Index both sides at each (market, player, book, timestamp) for de-vig.
        snapshots: dict[tuple, dict[str, float]] = {}
        for row in records:
            key = (row["market"], row["player"], row["book"], row["timestamp"])
            snapshots.setdefault(key, {})[row["side"]] = float(row["price"])

        stakes: dict[str, float] = {}
        pending: dict[str, dict] = {}
        reported: set[str] = set()
        for market, player in {(r["market"], r["player"]) for r in records}:
            if market in config.exclude_markets:  # a market the backtest excluded
                continue
            pid = resolve_player(player, name_to_id)
            if pid is None:
                if player not in reported:
                    unresolved.append({"game_id": str(game_id), "player": player, "market": market})
                    reported.add(player)
                continue
            if not props.has(pid, market):
                # Resolved to a real id, but the sim didn't produce this stat for
                # them (off the simulated depth chart, or a role/market
                # mismatch). Unpriceable — skip rather than crash.
                continue
            for side in ("over", "under"):
                best = _best_prop(game_lines, snapshots, props, pid, market, player, side, config)
                if best is None:
                    continue
                stake = stake_amount(
                    config.starting_bankroll, best["p_model"], best["price"], config.staking
                )
                if stake <= 0.0:
                    continue
                bet_key = f"{market}:{player}:{side}"
                stakes[bet_key] = stake
                pending[bet_key] = best

        if not stakes:
            continue
        # A player's over and under (and their markets) are correlated — group-cap
        # per game, as with the game slate.
        capped = apply_group_cap(stakes, config.group_cap_fraction, config.starting_bankroll)
        for bet_key, info in pending.items():
            stake = capped[bet_key]
            if stake <= 0.0:
                continue
            log.add(
                Bet(
                    game_id=str(game_id),
                    market=info["market"],
                    side=info["side"],
                    book=info["book"],
                    price=info["price"],
                    stake=stake,
                    p_model=info["p_model"],
                    point=info["point"],
                    timestamp=info["timestamp"],
                    player=info["player"],
                    p_fair=info.get("p_fair"),
                )
            )
    return log, unresolved


def _best_prop(
    game_lines: pd.DataFrame,
    snapshots: dict[tuple, dict[str, float]],
    props: PropDistributions,
    player_id: str,
    market: str,
    player: str,
    side: str,
    config: SlateConfig,
) -> dict | None:
    """Highest-EV qualifying opportunity for one (player, market, side)."""
    candidates = game_lines[
        (game_lines["market"] == market)
        & (game_lines["player"] == player)
        & (game_lines["side"] == side)
    ]
    best: dict | None = None
    for row in candidates.to_dict("records"):
        point = float(row["point"])
        bucket = snapshots.get((market, player, row["book"], row["timestamp"]), {})
        if side not in bucket or _OPPOSITE[side] not in bucket:
            continue
        fair = devig([bucket["over"], bucket["under"]], method=config.devig_method)
        p_fair = fair[0] if side == "over" else fair[1]
        p_model = (
            props.prob_over(player_id, market, point)
            if side == "over"
            else props.prob_under(player_id, market, point)
        )
        # Confidence calibration: shrink the model probability toward 0.5, exactly
        # as the game slate does. The MLB-era prop backtest found over-confidence
        # varies sharply by market, so the shrink is per-market; the football prop
        # backtest re-tunes it. shrink=1.0 leaves it untouched.
        shrink = config.shrink_for(market)
        if shrink != 1.0:
            p_model = 0.5 + shrink * (p_model - 0.5)
        signal = evaluate(
            p_model, float(row["price"]), p_fair, min_edge=config.min_edge_for(market)
        )
        if not signal.qualifies:
            continue
        if best is None or signal.ev > best["ev"]:
            best = {
                "market": market,
                "player": player,
                "side": side,
                "book": row["book"],
                "price": float(row["price"]),
                "point": point,
                "timestamp": row["timestamp"],
                "p_model": p_model,
                "p_fair": p_fair,
                "ev": signal.ev,
            }
    return best


def prop_slate_to_frame(log: BetLog) -> pd.DataFrame:
    """Render a prop :class:`BetLog` as a readable table (one row per staked bet)."""
    rows = [
        {
            "game_id": bet.game_id,
            "player": bet.player,
            "market": bet.market,
            "side": bet.side,
            "point": bet.point,
            "book": bet.book,
            "price": bet.price,
            "p_model": round(bet.p_model, 4),
            "p_fair": None if bet.p_fair is None else round(bet.p_fair, 4),
            "edge": None if bet.p_fair is None else round(bet.p_model - bet.p_fair, 4),
            "stake": round(bet.stake, 4),
        }
        for bet in log
    ]
    cols = ["game_id", "player", "market", "side", "point", "book", "price",
            "p_model", "p_fair", "edge", "stake"]
    return pd.DataFrame(rows, columns=cols)
