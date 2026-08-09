"""Parlays — correlated, sim-exact pricing and disciplined selection.

A parlay multiplies its legs' payouts, and every leg must win. Books price a
standard cross-game parlay as the product of the leg decimals — fair *only if
the legs are independent*. Legs inside one game are anything but: the total,
the spread, and the QB's passing-yards prop all move together. Pricing a
parlay honestly therefore needs the **joint** distribution, and the Monte Carlo
already is one: every leg of a game is a deterministic function of the same
simulated game, so evaluating the legs *per simulation* prices their
correlation exactly — no copula, no approximation.

Mechanics, per simulation: each leg resolves to **win** (multiplier = its
decimal price), **push** (multiplier = 1 — the leg drops out, the book
convention), or **loss** (the parlay pays 0). A game's legs multiply within one
simulation; games are independent, so expectations multiply *across* games
(never index-pairing two games' sample arrays — the model seeds every game's
generator identically, so cross-game index pairing would manufacture phantom
correlation).

The builder is deliberately conservative:

* Legs come only from bets that already cleared the single-bet edge gate — a
  parlay is a way to combine edges, never to launder a bad leg.
* Combined EV must clear its own (higher) threshold; combinatorics are capped.
* Stakes are fractional-Kelly at the parlay's odds with a tight absolute cap —
  parlay variance is brutal, and a mis-estimated leg compounds multiplicatively.
* Same-game combos are flagged ``same_game=True``: their EV is computed against
  the *product* payout, and books reprice correlated same-game parlays below
  that, so the printed EV is an upper bound on what is actually offered. The
  model's joint probability is still exact — the flag is about the price, not
  the probability.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from velocity.wagering.bet_log import Bet
from velocity.wagering.edge import expected_value
from velocity.wagering.odds import american_to_decimal


@runtime_checkable
class SimResult(Protocol):
    """What a game's simulation must expose to price parlay legs.

    ``home_score``/``away_score`` are per-simulation sampled score arrays
    (:class:`~velocity.models.simulate.GameSim` satisfies this). A result that
    also carries correlated player outcomes may additionally provide
    ``player_samples(player, market) -> np.ndarray`` to price prop legs; without
    it, prop legs are dropped (never guessed).
    """

    home_score: np.ndarray
    away_score: np.ndarray


# Leg outcome codes, per simulation.
_WIN, _PUSH, _LOSS = 1, 0, -1

# Game markets the sim's score arrays can grade.
_GAME_MARKETS = ("moneyline", "spread", "total")


@dataclass(frozen=True)
class ParlayLeg:
    """One leg of a parlay: a market position at a booked price.

    ``player`` is ``None`` for game markets and the *model player id* for props
    (resolution from the provider name happens before the leg is built, exactly
    as in the prop slate — never guessed here). ``point`` is ``None`` only for
    moneylines.
    """

    game_id: str
    market: str
    side: str
    price: float
    point: float | None = None
    player: str | None = None
    book: str | None = None
    label: str | None = None  # display name: the player, or the game's matchup

    def describe(self) -> str:
        """Compact human-readable leg description for the slate table."""
        name = self.label or self.player
        who = f"{name} " if name else ""
        pt = "" if self.point is None else f" {self.point:g}"
        return f"{who}{self.market} {self.side}{pt} ({self.price:+.0f})"


def leg_outcomes(leg: ParlayLeg, result: SimResult) -> np.ndarray:
    """Per-simulation outcome codes for one leg: 1 win / 0 push / −1 loss.

    Game markets read the sim's score arrays; prop legs read the player's sample
    array via ``result.player_samples``. Every array comes from the same
    simulated games, so codes for two legs of one game are jointly distributed
    exactly as the sim says.
    """
    if leg.player is not None:
        player_samples = getattr(result, "player_samples", None)
        if player_samples is None:
            raise KeyError(f"sim result carries no player samples for {leg.player!r}")
        samples = player_samples(leg.player, leg.market)
        if leg.point is None:
            raise ValueError("prop leg requires a point")
        over = samples > leg.point
        under = samples < leg.point
        hit, miss = (over, under) if leg.side == "over" else (under, over)
        return np.where(hit, _WIN, np.where(miss, _LOSS, _PUSH)).astype(np.int8)

    if leg.market not in _GAME_MARKETS:
        raise ValueError(f"unknown parlay market {leg.market!r}")
    margin = result.home_score - result.away_score
    if leg.market == "moneyline":
        edge = margin if leg.side == "home" else -margin
    elif leg.market == "spread":
        if leg.point is None:
            raise ValueError("spread leg requires a point")
        edge = (margin + leg.point) if leg.side == "home" else (-margin + leg.point)
    else:  # total
        if leg.point is None:
            raise ValueError("total leg requires a point")
        total = result.home_score + result.away_score
        edge = (total - leg.point) if leg.side == "over" else (leg.point - total)
    return np.where(edge > 0, _WIN, np.where(edge < 0, _LOSS, _PUSH)).astype(np.int8)


@dataclass(frozen=True)
class ParlayEvaluation:
    """Sim-exact verdict for one parlay.

    ``ev`` is expected profit per unit staked including push-reduction;
    ``p_all_win`` is the probability every leg wins outright;
    ``combined_decimal`` is the book's product payout the EV is measured
    against. ``same_game`` marks correlated combos whose quoted product payout
    books will reprice (the EV is then an upper bound).
    """

    p_all_win: float
    ev: float
    combined_decimal: float
    kelly: float
    same_game: bool


def evaluate_parlay(
    legs: Sequence[ParlayLeg],
    results_by_game: Mapping[str, SimResult],
) -> ParlayEvaluation:
    """Price a parlay exactly under the sims: correlated within a game, independent across.

    Per game, the legs' payout multipliers (win → decimal, push → 1, loss → 0)
    multiply *within each simulation* — correlation preserved. Across games the
    per-game expected multipliers multiply — independence, computed as such
    rather than by pairing unrelated sample indices.
    """
    if not legs:
        raise ValueError("a parlay needs at least one leg")
    by_game: dict[str, list[ParlayLeg]] = {}
    for leg in legs:
        by_game.setdefault(str(leg.game_id), []).append(leg)

    expected_multiplier = 1.0
    p_all_win = 1.0
    for game_id, game_legs in by_game.items():
        result = results_by_game[game_id]
        codes_by_leg = [leg_outcomes(leg, result) for leg in game_legs]
        multiplier = np.ones_like(codes_by_leg[0], dtype=float)
        all_win = np.ones_like(codes_by_leg[0], dtype=bool)
        for leg, codes in zip(game_legs, codes_by_leg, strict=True):
            decimal = american_to_decimal(leg.price)
            multiplier *= np.where(codes == _WIN, decimal, np.where(codes == _PUSH, 1.0, 0.0))
            all_win &= codes == _WIN
        expected_multiplier *= float(np.mean(multiplier))
        p_all_win *= float(np.mean(all_win))

    combined_decimal = float(np.prod([american_to_decimal(leg.price) for leg in legs]))
    ev = expected_multiplier - 1.0
    net = combined_decimal - 1.0
    kelly = ev / net if net > 0 else 0.0
    return ParlayEvaluation(
        p_all_win=p_all_win,
        ev=ev,
        combined_decimal=combined_decimal,
        kelly=kelly,
        same_game=any(len(v) > 1 for v in by_game.values()),
    )


@dataclass(frozen=True)
class ParlayConfig:
    """Selection thresholds, combinatoric caps, and parlay staking discipline.

    ``min_ev`` gates the *combined* EV — deliberately above the single-bet edge
    threshold, since stacking legs stacks estimation error. ``top_legs`` caps
    the candidate pool (ranked by single-leg EV) so enumeration stays tiny.
    ``max_stake_fraction`` is intentionally far below the single-bet cap.
    """

    min_legs: int = 2
    max_legs: int = 3
    top_legs: int = 8
    min_ev: float = 0.05
    max_parlays: int = 5
    kelly_fraction: float = 0.25
    max_stake_fraction: float = 0.01
    allow_same_game: bool = True

    def __post_init__(self) -> None:
        if not 2 <= self.min_legs <= self.max_legs:
            raise ValueError("need 2 <= min_legs <= max_legs")
        if not 0.0 < self.kelly_fraction <= 1.0:
            raise ValueError("kelly_fraction must be in (0, 1]")
        if not 0.0 < self.max_stake_fraction <= 1.0:
            raise ValueError("max_stake_fraction must be in (0, 1]")


@dataclass(frozen=True)
class ParlayTicket:
    """A recommended parlay: its legs, sim-exact evaluation, and stake."""

    legs: tuple[ParlayLeg, ...]
    evaluation: ParlayEvaluation
    stake: float


def legs_from_bets(
    bets: Sequence[Bet],
    results_by_game: Mapping[str, SimResult],
    name_to_id: Mapping[str, str] | None = None,
    *,
    game_labels: Mapping[str, str] | None = None,
) -> list[tuple[ParlayLeg, float]]:
    """Turn staked single bets into candidate parlay legs with their single EV.

    Only bets whose game has a sim (and, for props, whose player resolves to a
    simulated id) become legs — a leg we cannot price jointly is dropped, never
    guessed. ``game_labels`` (``game_id → "AWY@HOM"``) names game-market legs so
    a cross-game parlay reads unambiguously; prop legs keep the player's name.
    Returns ``(leg, single_ev)`` pairs; the EV ranks the pool.
    """
    from velocity.wagering.props_slate import resolve_player  # local: avoids cycle

    labels = game_labels or {}
    out: list[tuple[ParlayLeg, float]] = []
    for bet in bets:
        game_id = str(bet.game_id)
        if game_id not in results_by_game:
            continue
        player_id: str | None = None
        if bet.player is not None:
            player_id = resolve_player(bet.player, name_to_id or {})
            if player_id is None:
                continue
        leg = ParlayLeg(
            game_id=game_id,
            market=bet.market,
            side=bet.side,
            price=bet.price,
            point=bet.point,
            player=player_id,
            book=bet.book,
            label=bet.player if bet.player is not None else labels.get(game_id),
        )
        try:  # a leg the sim can't grade (missing stat array) is dropped
            leg_outcomes(leg, results_by_game[game_id])
        except (KeyError, ValueError):
            continue
        out.append((leg, expected_value(bet.p_model, bet.price)))
    return out


def _conflicts(a: ParlayLeg, b: ParlayLeg) -> bool:
    """Whether two legs cannot share a ticket.

    Same (game, market, player) twice is either a contradiction (over + under),
    a duplicate, or a both-sides hedge — none belongs in a parlay.
    """
    return a.game_id == b.game_id and a.market == b.market and a.player == b.player


def build_parlays(
    bets: Sequence[Bet],
    results_by_game: Mapping[str, SimResult],
    *,
    bankroll: float,
    name_to_id: Mapping[str, str] | None = None,
    game_labels: Mapping[str, str] | None = None,
    config: ParlayConfig | None = None,
) -> list[ParlayTicket]:
    """Select and stake parlays from the slate's qualifying single bets.

    The candidate pool is the top ``config.top_legs`` legs by single-bet EV;
    every ``min_legs``..``max_legs`` combination without conflicts is priced
    sim-exactly, kept if its combined EV clears ``config.min_ev``, and the best
    ``max_parlays`` (by EV) are staked with fractional Kelly under the parlay
    stake cap. Deterministic given the inputs.
    """
    config = config or ParlayConfig()
    pool = legs_from_bets(bets, results_by_game, name_to_id, game_labels=game_labels)
    pool.sort(key=lambda pair: pair[1], reverse=True)
    legs = [leg for leg, _ in pool[: config.top_legs]]

    evaluated: list[tuple[tuple[ParlayLeg, ...], ParlayEvaluation]] = []
    for size in range(config.min_legs, config.max_legs + 1):
        for combo in combinations(legs, size):
            if any(_conflicts(a, b) for a, b in combinations(combo, 2)):
                continue
            evaluation = evaluate_parlay(combo, results_by_game)
            if evaluation.same_game and not config.allow_same_game:
                continue
            if evaluation.ev < config.min_ev or evaluation.kelly <= 0.0:
                continue
            evaluated.append((combo, evaluation))

    evaluated.sort(key=lambda pair: pair[1].ev, reverse=True)
    tickets: list[ParlayTicket] = []
    for combo, evaluation in evaluated[: config.max_parlays]:
        fraction = min(config.kelly_fraction * evaluation.kelly, config.max_stake_fraction)
        stake = bankroll * fraction
        if stake <= 0.0:
            continue
        tickets.append(ParlayTicket(legs=combo, evaluation=evaluation, stake=stake))
    return tickets


def _decimal_to_american(decimal: float) -> float:
    """Combined decimal payout → American odds (for display)."""
    if decimal >= 2.0:
        return round((decimal - 1.0) * 100.0)
    return round(-100.0 / (decimal - 1.0))


def parlay_slate_to_frame(tickets: Sequence[ParlayTicket]) -> pd.DataFrame:
    """Render parlay tickets as a readable table (one row per parlay).

    ``legs_json`` carries the structured legs (game id, market, side, point,
    price, player id) so a later grader can settle the ticket leg by leg — the
    display string alone can't be graded.
    """
    rows = [
        {
            "legs": " + ".join(leg.describe() for leg in t.legs),
            "n_legs": len(t.legs),
            "price": _decimal_to_american(t.evaluation.combined_decimal),
            "decimal": round(t.evaluation.combined_decimal, 3),
            "p_win": round(t.evaluation.p_all_win, 4),
            "ev": round(t.evaluation.ev, 4),
            "same_game": t.evaluation.same_game,
            "stake": round(t.stake, 4),
            "legs_json": json.dumps([
                {
                    "game_id": leg.game_id,
                    "market": leg.market,
                    "side": leg.side,
                    "price": leg.price,
                    "point": leg.point,
                    "player": leg.player,
                    "label": leg.label,
                }
                for leg in t.legs
            ]),
        }
        for t in tickets
    ]
    cols = ["legs", "n_legs", "price", "decimal", "p_win", "ev", "same_game", "stake",
            "legs_json"]
    return pd.DataFrame(rows, columns=cols)
