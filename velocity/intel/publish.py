"""The publish gate — which plays are worth POSTING, not merely worth betting.

The staked slate and the public post are different products. The slate takes
every bet clearing the edge threshold, because a bankroll compounds on
volume. A post is a promise: it invites followers to keep a tally, and a
profitable model still shows red roughly every other day, so a post-worthy
play has to clear a much higher bar than a bettable one.

**"No picks is a pick."** This gate returns nothing on most days by design.
An empty wager post is the honest output of a quiet board, and it costs a
follower nothing — which is the whole point.

Three rules, in the order they bite:

1. **An edge CEILING, not just a floor.** Measured on our own graded record
   (docs/PUBLISH_GATE.md): the highest-edge quartile of our bets carried the
   *worst* closing-line value — mean CLV −0.048 against +0.037 in the lowest
   quartile, with corr(stake, CLV) = −0.35. That is adverse selection, not
   opportunity: when the model screams, it is usually the market knowing
   something we do not (a late scratch, a lineup change, a stale line). An
   edge above the ceiling is a data-quality alarm.
2. **Conviction, not arithmetic.** The intel layer must rate the bet at a
   publishable tier, and a vetoed bet is never posted regardless of edge.
3. **The market must not have moved against us.** If the line drifted away
   from our side between pricing and posting, the disagreement was probably
   ours to lose. This reads the same odds archive CLV grading uses.

Pure functions of frames and dataclasses; offline-testable, no network.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import pandas as pd

from velocity.intel.score import TIER_FLAGGED, Conviction

# Tiers a post may carry. "C" is bettable but never publishable.
PUBLISHABLE_TIERS = ("A",)

# The edge band, in model-minus-fair probability. The floor is a real edge;
# the ceiling is the adverse-selection guard the record argued for.
DEFAULT_MIN_EDGE = 0.030
DEFAULT_MAX_EDGE = 0.120
# Conviction floor on the intel composite. Tier A only needs 0.65, and the
# blend (0.4*edge + 0.6*context) lets a bet reach that on EDGE ALONE with
# neutral context — which is precisely the adverse-selection profile §2 of
# docs/PUBLISH_GATE.md warns about. Publishing demands more than bettable.
DEFAULT_MIN_CONVICTION = 0.72
# ...and the context must actually corroborate, not merely fail to object.
# This is the "conviction, not arithmetic" rule with teeth: a big edge no
# signal supports is the exact shape of a line we have mispriced.
DEFAULT_MIN_CONTEXT = 0.05
# A guardrail, not a quota. "No picks is a pick" means the floor is zero;
# this caps the ceiling so one freak board cannot dump 30 plays into a feed
# that is supposed to read as high-conviction.
DEFAULT_MAX_PLAYS = 5
# How far the market may move against us (in probability terms, from the
# price we shopped to the newest price on the board) before the play is
# withdrawn. A couple of cents of drift is noise; a real move is a message.
DEFAULT_MAX_ADVERSE_DRIFT = 0.015


@dataclass(frozen=True)
class GateResult:
    """One bet's publish verdict, with the reason it failed when it did."""

    published: bool
    reason: str  # "" when published; else the rule that rejected it
    edge: float | None = None
    tier: str | None = None
    drift: float | None = None


def _implied_probability(price: float) -> float | None:
    """American odds → implied probability (with the vig still in)."""
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    if -100.0 < value < 100.0:
        return None
    if value > 0:
        return 100.0 / (value + 100.0)
    return -value / (-value + 100.0)


def adverse_drift(
    bet_price: float, current_price: float | None
) -> float | None:
    """How far the market moved AGAINST our side, in probability terms.

    Positive means the market moved AWAY from our side since we shopped it
    (it now implies our side is less likely) — the disagreement was probably
    ours to lose. Negative means it moved toward us, which is the shape of
    positive closing-line value and a good sign, never a reason to withdraw.
    ``None`` when either price cannot be read — an unknown drift never
    rejects a play on its own.
    """
    ours = _implied_probability(bet_price)
    theirs = _implied_probability(current_price) if current_price is not None else None
    if ours is None or theirs is None:
        return None
    return float(ours - theirs)


def current_prices(lines: pd.DataFrame | None) -> dict[tuple[str, str, str], float]:
    """``(game_id, market, side)`` → the newest price on the board.

    Reads the canonical lines frame the slate already carries, so the gate
    needs no extra fetch.
    """
    if lines is None or lines.empty:
        return {}
    frame = lines.dropna(subset=["price"]).copy()
    if frame.empty:
        return {}
    if "timestamp" in frame.columns:
        frame = frame.sort_values("timestamp")
    index: dict[tuple[str, str, str], float] = {}
    for row in frame.to_dict("records"):
        key = (str(row["game_id"]), str(row["market"]), str(row["side"]))
        index[key] = float(row["price"])  # later rows overwrite → newest wins
    return index


def gate_bet(
    conviction: Conviction,
    *,
    current_price: float | None = None,
    min_edge: float = DEFAULT_MIN_EDGE,
    max_edge: float = DEFAULT_MAX_EDGE,
    max_adverse_drift: float = DEFAULT_MAX_ADVERSE_DRIFT,
    publishable_tiers: Sequence[str] = PUBLISHABLE_TIERS,
    min_conviction: float = DEFAULT_MIN_CONVICTION,
    min_context: float = DEFAULT_MIN_CONTEXT,
) -> GateResult:
    """Decide whether one judged bet is post-worthy."""
    bet = conviction.bet
    tier = conviction.tier
    if tier == TIER_FLAGGED or conviction.vetoed:
        return GateResult(False, "vetoed by the intel layer", tier=tier)
    if tier not in publishable_tiers:
        return GateResult(False, f"tier {tier} below publishable", tier=tier)
    if conviction.score < min_conviction:
        return GateResult(False,
                          f"conviction {conviction.score:.2f} below "
                          f"{min_conviction:.2f}", tier=tier)
    if conviction.context_score < min_context:
        return GateResult(False,
                          f"context {conviction.context_score:+.2f} does not "
                          "corroborate the edge", tier=tier)

    p_fair = bet.p_fair
    if p_fair is None or pd.isna(p_fair):
        return GateResult(False, "no de-vigged fair price to measure against",
                          tier=tier)
    edge = float(bet.p_model) - float(p_fair)
    if edge < min_edge:
        return GateResult(False, f"edge {edge:.3f} below floor {min_edge:.3f}",
                          edge=edge, tier=tier)
    if edge > max_edge:
        # The finding that motivated this gate: our biggest edges had the
        # worst CLV. Treat an outsized edge as a data alarm, not a green light.
        return GateResult(False,
                          f"edge {edge:.3f} above ceiling {max_edge:.3f} "
                          "(adverse-selection guard)", edge=edge, tier=tier)

    drift = adverse_drift(bet.price, current_price)
    if drift is not None and drift > max_adverse_drift:
        return GateResult(False,
                          f"market moved {drift:.3f} against us since pricing",
                          edge=edge, tier=tier, drift=drift)
    return GateResult(True, "", edge=edge, tier=tier, drift=drift)


def publish_slate(
    convictions: Iterable[Conviction],
    lines: pd.DataFrame | None = None,
    *,
    min_edge: float = DEFAULT_MIN_EDGE,
    max_edge: float = DEFAULT_MAX_EDGE,
    max_adverse_drift: float = DEFAULT_MAX_ADVERSE_DRIFT,
    publishable_tiers: Sequence[str] = PUBLISHABLE_TIERS,
    min_conviction: float = DEFAULT_MIN_CONVICTION,
    min_context: float = DEFAULT_MIN_CONTEXT,
    max_plays: int = DEFAULT_MAX_PLAYS,
) -> tuple[list[Conviction], pd.DataFrame]:
    """Split judged bets into the publishable set and a full audit frame.

    Returns ``(published, audit)``. The audit frame carries every candidate
    with its verdict and reason, so a quiet night is explainable rather than
    mysterious — the same discipline the vetoed-picks table already follows.
    """
    prices = current_prices(lines)
    rows: list[dict[str, object]] = []
    passed: list[tuple[int, Conviction]] = []
    for index, conviction in enumerate(convictions):
        bet = conviction.bet
        key = (str(bet.game_id), str(bet.market), str(bet.side))
        result = gate_bet(
            conviction, current_price=prices.get(key),
            min_edge=min_edge, max_edge=max_edge,
            max_adverse_drift=max_adverse_drift,
            publishable_tiers=publishable_tiers,
            min_conviction=min_conviction, min_context=min_context,
        )
        if result.published:
            passed.append((index, conviction))
        rows.append({
            "game_id": str(bet.game_id), "market": str(bet.market),
            "side": str(bet.side), "player": bet.player,
            "price": float(bet.price), "stake": float(bet.stake),
            "edge": result.edge, "tier": result.tier, "drift": result.drift,
            # Banked so the thresholds can be CALIBRATED from real boards
            # rather than argued about — see docs/PUBLISH_GATE.md §6.
            "conviction": float(conviction.score),
            "context": float(conviction.context_score),
            "published": result.published, "reason": result.reason,
        })

    # Highest conviction first — the post's running order — then the ceiling.
    passed.sort(key=lambda pair: pair[1].score, reverse=True)
    if max_plays >= 0 and len(passed) > max_plays:
        for index, _conviction in passed[max_plays:]:
            rows[index]["published"] = False
            rows[index]["reason"] = f"outside the top {max_plays} by conviction"
        passed = passed[:max_plays]

    columns = ["game_id", "market", "side", "player", "price", "stake",
               "edge", "tier", "drift", "conviction", "context",
               "published", "reason"]
    audit = pd.DataFrame(rows, columns=columns)
    return [conviction for _index, conviction in passed], audit


def gate_summary(audit: pd.DataFrame) -> str:
    """A one-line explanation of the night, publishable or not."""
    if audit.empty:
        return "no bets on the board"
    n_pub = int(audit["published"].sum())
    if n_pub:
        return f"{n_pub} of {len(audit)} candidates cleared the publish gate"
    reasons = audit["reason"].value_counts()
    top = ", ".join(f"{reason} ({count})"
                    for reason, count in list(reasons.items())[:2])
    return f"no plays cleared the gate — {top}"
