"""Pick'em board builder — prop lines + the correlated sim → ranked slips.

The bridge between the live prop slate and the slip-EV engine
(:mod:`velocity.wagering.pickem`). For each (game, market, player) on the
prop board it devigs the freshest two-sided quote into a fair probability,
resolves the player into the game's correlated sim, and emits a candidate
:class:`~velocity.wagering.pickem.Leg`; the slip search then ranks
combinations across the payout shapes.

**Calibrated correlation** — the honest joint model. The sim's marginals are
our model's opinion; the devigged book probability is the better-calibrated
marginal until the prop model wins its own lab. So each leg's hit threshold
is moved to the sim quantile that reproduces the *book's* fair probability,
leaving the sim to contribute exactly the thing the book cannot price: the
correlation between legs. Slip EVs are therefore book-marginal ×
model-correlation — conservative on marginals, sharp on stacks.

**Until the pick'em transport lands** (docs/DATA_PROVIDERS.md) the board
line is assumed to be the book's own line — the divergence half of the edge
(board line vs sharp line) prices as zero. Once real board snapshots flow,
the same builder takes the board's line instead and the divergence edge
switches on with no structural change.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import numpy as np
import pandas as pd

from velocity.wagering.devig import devig
from velocity.wagering.pickem import PAYOUTS, Leg, best_slips
from velocity.wagering.props_slate import resolve_player

__all__ = ["PickemSim", "build_pickem_board"]

# The slip shapes worth publishing: the big flexes carry the lowest
# breakevens and the powers monetize correlation hardest. flex-2 is omitted
# (its 2.0x table is strictly dominated for any p above a coin flip).
DEFAULT_TABLES = ("power-2", "power-3", "power-4", "flex-4", "flex-5", "flex-6")


class PickemSim(Protocol):
    """What the builder needs from a game's prop simulation."""

    def has(self, player_id: str, market: str) -> bool: ...

    def player_samples(self, player: str, market: str) -> np.ndarray: ...

    def prob_over(self, player_id: str, market: str, point: float) -> float: ...


def _freshest_two_sided(lines: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, market, player): the newest quote with both sides.

    Only a (book, timestamp) bucket carrying over *and* under at the same
    point can be devigged; among those the newest timestamp wins (freshest
    number closest to what a board would mirror).
    """
    keys = ["game_id", "market", "player", "book", "timestamp", "point"]
    pivot = (
        lines.pivot_table(
            index=keys, columns="side", values="price", aggfunc="last"
        )
        .reset_index()
    )
    if "over" not in pivot.columns or "under" not in pivot.columns:
        return pd.DataFrame()
    both = pivot.dropna(subset=["over", "under"])
    if both.empty:
        return both
    both = both.sort_values("timestamp")
    return both.groupby(["game_id", "market", "player"], as_index=False).last()


def build_pickem_board(
    props_by_game: Mapping[str, PickemSim],
    prop_lines: pd.DataFrame,
    name_to_id: Mapping[str, str],
    *,
    tables: tuple[str, ...] = DEFAULT_TABLES,
    devig_method: str = "multiplicative",
    min_leg_p: float = 0.54,
    max_legs: int = 12,
    min_ev: float = 0.0,
    top: int = 10,
    max_per_game: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The pick'em legs board and the ranked slips built from it.

    Returns ``(legs, slips)``. Legs carry both probabilities — ``p_book``
    (devigged, the staking marginal) and ``p_model`` (the sim's own opinion,
    recorded for the future prop-model lab) — plus the side that the fair
    probability favors. ``min_leg_p`` trims legs no slip shape can use (the
    softest breakeven in the standard tables sits around 0.54). Slips are
    ranked by calibrated-correlation EV; an empty frame simply means nothing
    cleared ``min_ev`` today, which is a result, not a failure.
    """
    if prop_lines is None or prop_lines.empty or not props_by_game:
        empty = pd.DataFrame()
        return empty, empty

    legs: list[Leg] = []
    rows: list[dict[str, object]] = []
    shifted: dict[tuple[str, str], np.ndarray] = {}
    for row in _freshest_two_sided(prop_lines).to_dict("records"):
        sim = props_by_game.get(str(row["game_id"]))
        if sim is None:
            continue
        pid = resolve_player(str(row["player"]), name_to_id)
        market = str(row["market"])
        if pid is None or not sim.has(pid, market):
            continue
        point = float(row["point"])
        over_price, under_price = float(row["over"]), float(row["under"])
        if devig_method == "worst_case":
            # Side from the multiplicative baseline; magnitude only as large
            # as every devig method endorses (velocity.wagering.pickem
            # ``conservative_leg``) — a leg near a payout breakeven survives
            # only when multiplicative, Shin, and power all clear it.
            from velocity.wagering.pickem import conservative_leg

            side, p_book = conservative_leg(over_price, under_price)
            p_over = p_book if side == "over" else 1.0 - p_book
        else:
            fair = devig([over_price, under_price], method=devig_method)
            p_over = float(fair[0])
            side = "over" if p_over >= 0.5 else "under"
            p_book = p_over if side == "over" else 1.0 - p_over
        if not 0.0 < p_book < 1.0 or p_book < min_leg_p:
            continue
        display = str(row["player"])
        key = (display, market)
        if key in shifted:  # one leg per (player, market) — labels key samples
            continue
        samples = np.asarray(sim.player_samples(pid, market), dtype=float)
        # Calibrate: move the threshold to the sim quantile that reproduces
        # the book's fair over-probability, then shift the samples so the
        # engine's `> line` test lands on that threshold. Marginals become
        # the book's; correlation stays the sim's.
        threshold = float(np.quantile(samples, 1.0 - p_over))
        shifted[key] = samples - threshold + point
        p_model = float(sim.prob_over(pid, market, point))
        legs.append(Leg(player=display, stat=market, line=point, side=side, p=p_book,
                        game_id=str(row["game_id"])))
        rows.append({
            "game_id": str(row["game_id"]),
            "player": display,
            "player_id": pid,
            "market": market,
            "line": point,
            "side": side,
            "p_book": p_book,
            "p_model": p_model if side == "over" else 1.0 - p_model,
            "book": row["book"],
            "timestamp": row["timestamp"],
        })

    legs_frame = pd.DataFrame(
        rows, columns=["game_id", "player", "player_id", "market", "line", "side",
                       "p_book", "p_model", "book", "timestamp"]
    ).sort_values("p_book", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()
    if not legs:
        return legs_frame, pd.DataFrame()

    slips = best_slips(
        legs,
        samples_fn=lambda player, stat: shifted[(player, stat)],
        tables=tuple(name for name in tables if name in PAYOUTS),
        max_legs=max_legs,
        min_ev=min_ev,
        top=top,
        max_per_game=max_per_game,
    )
    return legs_frame, slips
