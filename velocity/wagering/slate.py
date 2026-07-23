"""Slate orchestration — projections + lines → staked, logged bets.

This is the end-to-end wagering path, wiring the pieces together for a slate of
games:

1. Take each game's :class:`~velocity.models.game_nfl.GameProjection` and, for
   every market and side, read the model's probability straight off the
   simulated distribution.
2. Consider only lines observed **before kickoff** (point-in-time correctness —
   never bet a price we could not actually have taken), and **shop** across books
   and numbers, keeping the single best-EV opportunity per market side.
3. **De-vig** each opportunity against its paired opposite side to get the fair
   probability, then measure edge/EV and keep only what clears the threshold.
4. **Stake** survivors with fractional Kelly, capped per bet and per game
   (a game's correlated bets share a group cap).
5. **Log** each bet with its closing line so CLV can be tracked.

The result is a :class:`~velocity.wagering.bet_log.BetLog`; grade it with
``.settle(games)`` for a reproducible bankroll curve.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from velocity.models.game_nfl import GameProjection
from velocity.store import pit
from velocity.wagering.bet_log import Bet, BetLog
from velocity.wagering.devig import devig
from velocity.wagering.edge import evaluate
from velocity.wagering.staking import StakingConfig, apply_group_cap, stake_amount

_MARKET_SIDES = {
    "spread": ("home", "away"),
    "total": ("over", "under"),
    "moneyline": ("home", "away"),
}
_OPPOSITE = {"home": "away", "away": "home", "over": "under", "under": "over"}


@dataclass(frozen=True)
class SlateConfig:
    """Knobs for building a slate of bets."""

    devig_method: str = "multiplicative"
    min_edge: float = 0.02
    starting_bankroll: float = 100.0
    group_cap_fraction: float = 0.10
    staking: StakingConfig = field(default_factory=StakingConfig)
    # Backtest excludes the closing observation from entry candidates so CLV is
    # measured against a line we did *not* bet. A live slate has no separate close
    # yet (all we have is the current snapshot), so it must keep every observation
    # as a candidate; CLV is measured later against the true closing snapshot.
    exclude_closing: bool = True


def model_probability(
    proj: GameProjection, market: str, side: str, point: float | None
) -> float:
    """The model's probability for one (market, side, point), read off the sim."""
    if market == "moneyline":
        p_home = proj.p_home_win()
        return p_home if side == "home" else 1.0 - p_home
    if point is None:
        raise ValueError(f"{market} requires a point")
    margin = proj.sim.margin
    total = proj.sim.total
    if market == "spread":
        covered = (margin + point) > 0 if side == "home" else (-margin + point) > 0
        return float(np.mean(covered))
    if market == "total":
        hit = total > point if side == "over" else total < point
        return float(np.mean(hit))
    raise ValueError(f"unknown market {market!r}")


def _fair_probability(
    bucket: dict[str, tuple[float, float | None]],
    side: str,
    method: str,
) -> float | None:
    """De-vig our side against its paired opposite in the same market snapshot."""
    opp = _OPPOSITE[side]
    if side not in bucket or opp not in bucket:
        return None
    our_price = bucket[side][0]
    opp_price = bucket[opp][0]
    fair = devig([our_price, opp_price], method=method)
    return fair[0]


def build_slate(
    projections: dict[str, GameProjection],
    lines: pd.DataFrame,
    games: pd.DataFrame,
    config: SlateConfig | None = None,
) -> BetLog:
    """Build a :class:`BetLog` of staked bets from projections and a line archive."""
    config = config or SlateConfig()
    pre = pit.lines_before_kickoff(lines, games)
    closing = pit.closing_line(lines, games)
    # Backtest excludes the closing observation so CLV is measured against a line
    # we did *not* bet (else "betting the close" reports ~0 CLV). A live snapshot
    # is the only board, so nothing is excluded — CLV is measured later, against
    # the real closing snapshot from the archive.
    excluded = set(closing["line_id"]) if config.exclude_closing else set()
    entries = pre[~pre["line_id"].isin(excluded)]
    log = BetLog()

    for game_id, proj in projections.items():
        game_lines = entries[entries["game_id"] == game_id]
        if game_lines.empty:
            continue

        # For de-vig we need both sides at the same (book, timestamp); index them.
        snapshots: dict[tuple, dict[str, tuple[float, float | None]]] = {}
        for row in game_lines.to_dict("records"):
            key = (row["market"], row["book"], row["timestamp"])
            point = None if pd.isna(row["point"]) else float(row["point"])
            snapshots.setdefault(key, {})[row["side"]] = (float(row["price"]), point)

        game_stakes: dict[str, float] = {}
        pending: dict[str, dict] = {}

        for market, sides in _MARKET_SIDES.items():
            for side in sides:
                best = _best_opportunity(
                    game_lines, snapshots, proj, market, side, config
                )
                if best is None:
                    continue
                stake = stake_amount(
                    config.starting_bankroll,
                    best["p_model"],
                    best["price"],
                    config.staking,
                )
                if stake <= 0.0:
                    continue
                bet_key = f"{market}:{side}"
                game_stakes[bet_key] = stake
                pending[bet_key] = best

        if not game_stakes:
            continue

        # Correlated bets on one game share a group cap.
        capped = apply_group_cap(game_stakes, config.group_cap_fraction, config.starting_bankroll)
        for bet_key, info in pending.items():
            stake = capped[bet_key]
            if stake <= 0.0:
                continue
            close = _closing_for(closing, game_id, info["market"], info["side"], info["book"])
            log.add(
                Bet(
                    game_id=game_id,
                    market=info["market"],
                    side=info["side"],
                    book=info["book"],
                    price=info["price"],
                    stake=stake,
                    p_model=info["p_model"],
                    point=info["point"],
                    timestamp=info["timestamp"],
                    closing_price=close[0] if close else None,
                    closing_point=close[1] if close else None,
                    p_fair=info.get("p_fair"),
                )
            )

    return log


def _best_opportunity(
    game_lines: pd.DataFrame,
    snapshots: dict[tuple, dict[str, tuple[float, float | None]]],
    proj: GameProjection,
    market: str,
    side: str,
    config: SlateConfig,
) -> dict | None:
    """Highest-EV qualifying opportunity for one market side (shops book/number/time)."""
    candidates = game_lines[(game_lines["market"] == market) & (game_lines["side"] == side)]
    best: dict | None = None
    for row in candidates.to_dict("records"):
        point = None if pd.isna(row["point"]) else float(row["point"])
        bucket = snapshots.get((market, row["book"], row["timestamp"]), {})
        p_fair = _fair_probability(bucket, side, config.devig_method)
        if p_fair is None:
            continue
        p_model = model_probability(proj, market, side, point)
        signal = evaluate(p_model, float(row["price"]), p_fair, min_edge=config.min_edge)
        if not signal.qualifies:
            continue
        if best is None or signal.ev > best["ev"]:
            best = {
                "market": market,
                "side": side,
                "book": row["book"],
                "price": float(row["price"]),
                "point": point,
                "timestamp": row["timestamp"],
                "p_model": p_model,
                "p_fair": p_fair,
                "edge": signal.edge,
                "ev": signal.ev,
            }
    return best


def _closing_for(
    closing: pd.DataFrame, game_id: str, market: str, side: str, book: str
) -> tuple[float, float | None] | None:
    """The closing price/point for a market side, preferring the same book."""
    match = closing[
        (closing["game_id"] == game_id)
        & (closing["market"] == market)
        & (closing["side"] == side)
    ]
    if match.empty:
        return None
    same_book = match[match["book"] == book]
    row = (same_book if not same_book.empty else match).iloc[-1]
    point = None if pd.isna(row["point"]) else float(row["point"])
    return float(row["price"]), point
