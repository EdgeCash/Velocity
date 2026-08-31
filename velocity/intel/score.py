"""Conviction scoring — model edge and context signals folded into one verdict.

The intelligence layer's contract with the wagering engine is strict:

* **It can never promote.** Only bets that already cleared the EV gate
  (``edge ≥ min_edge`` and ``EV > 0``) reach this layer; a negative-EV bet is
  never resurrected by good vibes about a matchup.
* **It can confirm, demote, or veto.** Context that agrees with the model
  raises conviction; context that contradicts it lowers conviction; a veto
  (QB out, prop player ruled out) flags the bet as unplayable on information
  the pricing model has not seen.
* **It never touches stakes.** Kelly sizing is calibrated against the model's
  probabilities; the layer ranks and tiers, and a veto removes a bet from the
  recommended sets — it does not rescale anything mid-pipeline.

The composite score lives in ``[0, 1]``: an edge component (how far past the
threshold the model's edge sits) blended with the weighted mean of the active
signals. Signals that abstain drop out of the weighting entirely — a bet
judged by two signals is not diluted by the three that had nothing to say.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from velocity.intel.context import GameContext
from velocity.intel.signals import SignalResult
from velocity.wagering.bet_log import Bet


class Signal(Protocol):
    """Anything with a name that can judge a bet against a game's context."""

    @property
    def name(self) -> str: ...

    def evaluate(self, bet: Bet, ctx: GameContext) -> SignalResult | None: ...


# Relative importance of each signal when it speaks. Matchup and injuries
# carry the most weight (units are the model's own currency; outs are the one
# thing the model cannot see), form and rest confirm at the margin.
DEFAULT_SIGNAL_WEIGHTS: Mapping[str, float] = {
    "matchup": 0.35,
    "form": 0.25,
    "rest": 0.15,
    "injury": 0.25,
    "availability": 0.45,
    "prop_matchup": 0.35,
    # An independent public rating system (SP+) agreeing or arguing — real
    # outside information, but last season's book in-season, so it confirms
    # at the margin rather than deciding.
    "external_rating": 0.25,
    # BettingPros' live prop projection block: current-week outside judgment
    # on the exact line, so it carries a bit more weight than a season-old
    # rating — still confirmation, never a promoter.
    "prop_external": 0.30,
}

TIER_FLAGGED = "X"


@dataclass(frozen=True)
class IntelConfig:
    """Knobs for turning edge + signals into a conviction tier."""

    # Blend: composite = edge_weight·edge_score + (1 − edge_weight)·context_score.
    edge_weight: float = 0.4
    # The probability edge that maps to a full edge score of 1.0. 0.05 = a bet
    # holding 5+ points of probability edge maxes the component.
    edge_reference: float = 0.05
    signal_weights: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SIGNAL_WEIGHTS)
    )
    # Composite thresholds for the tiers (A ≥ tier_a > B ≥ tier_b > C).
    tier_a: float = 0.65
    tier_b: float = 0.45
    # False disables vetoes: a would-be veto becomes an ordinary −1 signal.
    veto_enabled: bool = True

    def weight_for(self, name: str) -> float:
        return float(self.signal_weights.get(name, 0.0))


@dataclass(frozen=True)
class Conviction:
    """One bet's full intelligence verdict."""

    bet: Bet
    edge_score: float  # [0, 1] — how far past the gate the model edge sits
    context_score: float  # [-1, +1] — weighted mean of the active signals
    score: float  # [0, 1] — the blended composite
    tier: str  # "A" / "B" / "C", or TIER_FLAGGED when vetoed
    signals: tuple[SignalResult, ...] = ()

    @property
    def vetoed(self) -> bool:
        return self.tier == TIER_FLAGGED

    def rationale(self, limit: int = 3) -> str:
        """The strongest evidence lines, vetoes first, strongest signals next."""
        vetoes = [s for s in self.signals if s.veto]
        rest = sorted(
            (s for s in self.signals if not s.veto), key=lambda s: abs(s.score), reverse=True
        )
        lines = [f"VETO — {s.rationale}" for s in vetoes]
        lines += [s.rationale for s in rest]
        return " | ".join(lines[:limit]) if lines else "no context signal spoke"


def assess(
    bet: Bet,
    ctx: GameContext,
    signals: Sequence[Signal],
    config: IntelConfig | None = None,
) -> Conviction:
    """Judge one qualifying bet against its game's context."""
    config = config or IntelConfig()

    edge = None if bet.p_fair is None else bet.p_model - bet.p_fair
    # A bet with no banked fair probability scores neutral — not rewarded.
    edge_score = 0.5 if edge is None else max(0.0, min(1.0, edge / config.edge_reference))

    results: list[SignalResult] = []
    for signal in signals:
        verdict = signal.evaluate(bet, ctx)
        if verdict is None:
            continue
        if verdict.veto and not config.veto_enabled:
            verdict = SignalResult(verdict.name, verdict.score, verdict.rationale, veto=False)
        results.append(verdict)

    weighted = [(config.weight_for(r.name), r.score) for r in results]
    active = [(w, s) for w, s in weighted if w > 0]
    total_weight = sum(w for w, _ in active)
    context_score = (
        sum(w * s for w, s in active) / total_weight if total_weight > 0 else 0.0
    )

    composite = config.edge_weight * edge_score + (1.0 - config.edge_weight) * (
        (context_score + 1.0) / 2.0
    )

    if any(r.veto for r in results):
        tier = TIER_FLAGGED
    elif composite >= config.tier_a:
        tier = "A"
    elif composite >= config.tier_b:
        tier = "B"
    else:
        tier = "C"

    return Conviction(
        bet=bet,
        edge_score=edge_score,
        context_score=context_score,
        score=composite,
        tier=tier,
        signals=tuple(results),
    )


def assess_bets(
    bets: Iterable[Bet],
    contexts: Mapping[str, GameContext],
    signals: Sequence[Signal],
    config: IntelConfig | None = None,
) -> list[Conviction]:
    """Judge every bet that has a context; bets without one are skipped.

    A game the context library could not cover (unmatched team names, missing
    data) yields no verdict rather than a fabricated neutral one — the caller
    reports those bets as un-assessed, mirroring the slate's "unresolved,
    never guessed" discipline.
    """
    out: list[Conviction] = []
    for bet in bets:
        ctx = contexts.get(str(bet.game_id))
        if ctx is None:
            continue
        out.append(assess(bet, ctx, signals, config))
    return out
