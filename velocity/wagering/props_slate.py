"""Player-prop slate — model prop distributions + prop lines → staked bets.

The game-market analogue of :mod:`velocity.wagering.slate`, for player props. For
each game it takes the model's :class:`~velocity.models.props_mlb.BaseballProps`
(the empirical per-player distributions from the sim) and the provider's prop
board, resolves each provider player name to a model player id, de-vigs each
over/under pair, measures edge, and stakes survivors with the same
fractional-Kelly-plus-group-cap discipline. Unresolved players are skipped and
reported, never guessed.

CLV is not measured here — a live snapshot is the only board — so bets are logged
without a close; the prop-line archive (the collector) is what a later backtest
grades them against.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import pandas as pd

from velocity.models.game_mlb import MLBGameModel
from velocity.models.props_mlb import BaseballProps
from velocity.wagering.bet_log import Bet, BetLog
from velocity.wagering.devig import devig
from velocity.wagering.edge import evaluate
from velocity.wagering.live import MLB_TEAM_ALIASES, resolve_team
from velocity.wagering.slate import SlateConfig
from velocity.wagering.staking import apply_group_cap, stake_amount

_OPPOSITE = {"over": "under", "under": "over"}


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def build_name_index(*stats_frames: pd.DataFrame) -> dict[str, str]:
    """Map normalized player name → player id from ``BaseballStats`` frames."""
    index: dict[str, str] = {}
    for frame in stats_frames:
        for pid, name in zip(frame["player_id"], frame["player_name"], strict=False):
            index[_normalize(str(name))] = str(pid)
    return index


def resolve_player(name: str, name_to_id: Mapping[str, str]) -> str | None:
    """Provider player name → model player id (normalized match), or ``None``."""
    return name_to_id.get(_normalize(name))


def build_prop_slate(
    props_by_game: Mapping[str, BaseballProps],
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
            pid = resolve_player(player, name_to_id)
            if pid is None:
                if player not in reported:
                    unresolved.append({"game_id": str(game_id), "player": player, "market": market})
                    reported.add(player)
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
                )
            )
    return log, unresolved


def mlb_prop_slate(
    model: MLBGameModel,
    events: pd.DataFrame,
    prop_lines: pd.DataFrame,
    name_to_id: Mapping[str, str],
    *,
    aliases: Mapping[str, str] | None = None,
    config: SlateConfig | None = None,
) -> tuple[BetLog, list[dict[str, str]]]:
    """Prop slate for an MLB board: resolve each event, simulate, price its props.

    For each event whose teams resolve to model clubs, the per-game
    :class:`~velocity.models.props_mlb.BaseballProps` come from one simulation, and
    :func:`build_prop_slate` prices the board against them.
    """
    alias_map = dict(MLB_TEAM_ALIASES if aliases is None else aliases)
    codes = list(alias_map.values())

    props_by_game: dict[str, BaseballProps] = {}
    for event in events.to_dict("records"):
        home = resolve_team(str(event["home_team"]), codes, alias_map)
        away = resolve_team(str(event["away_team"]), codes, alias_map)
        if home is None or away is None:
            continue
        if home not in model.teams or away not in model.teams:
            continue
        props_by_game[str(event["game_id"])] = BaseballProps(model.project(home, away).result)

    return build_prop_slate(props_by_game, prop_lines, name_to_id, config)


def _best_prop(
    game_lines: pd.DataFrame,
    snapshots: dict[tuple, dict[str, float]],
    props: BaseballProps,
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
        signal = evaluate(p_model, float(row["price"]), p_fair, min_edge=config.min_edge)
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
            "stake": round(bet.stake, 4),
        }
        for bet in log
    ]
    cols = ["game_id", "player", "market", "side", "point", "book", "price", "p_model", "stake"]
    return pd.DataFrame(rows, columns=cols)
