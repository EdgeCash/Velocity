"""Pick sets — the intelligence layer's output, shaped for a human to act on.

Convictions partition into named sets:

* **Prime** (tier A) — positive EV *and* the context agrees. The card.
* **Solid** (tier B) — positive EV with neutral-to-mixed context.
* **Model-only** (tier C) — cleared the EV gate but the context leans against;
  still +EV by the model, listed for the operator who trusts the sim outright.
* **Flagged** (vetoed) — positive EV on paper, but the layer found information
  the pricing model cannot see (QB out, prop player ruled out). Not
  recommended; shown with the veto reason so the human can re-check the board.

Every pick carries its evidence lines, so the sets read as an argued card,
not a bare list of sides.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import pandas as pd

from velocity.intel.score import TIER_FLAGGED, Conviction

_SETS: tuple[tuple[str, str, str], ...] = (
    ("A", "prime", "Prime — model edge + confirming context"),
    ("B", "solid", "Solid — model edge, neutral context"),
    ("C", "model_only", "Model-only — context leans against; +EV on the sim alone"),
    (TIER_FLAGGED, "flagged", "Flagged — vetoed on information the model can't see"),
)


@dataclass(frozen=True)
class PickSet:
    """One named, ordered set of convictions."""

    key: str
    label: str
    picks: tuple[Conviction, ...]

    @property
    def recommended(self) -> bool:
        return self.key != "flagged"


def build_pick_sets(convictions: Iterable[Conviction]) -> tuple[PickSet, ...]:
    """Partition convictions into the named sets, strongest first within each."""
    ranked = sorted(convictions, key=lambda c: c.score, reverse=True)
    sets = []
    for tier, key, label in _SETS:
        members = tuple(c for c in ranked if c.tier == tier)
        sets.append(PickSet(key=key, label=label, picks=members))
    return tuple(sets)


def _bet_label(conviction: Conviction) -> str:
    bet = conviction.bet
    point = "" if bet.point is None else f" {bet.point:g}"
    player = f"{bet.player} " if bet.player else ""
    return f"{player}{bet.market} {bet.side}{point} @ {bet.price:g} ({bet.book})"


def intel_frame(convictions: Sequence[Conviction]) -> pd.DataFrame:
    """Flat frame of every verdict — the slate parquet's intelligence companion."""
    rows = []
    for c in convictions:
        bet = c.bet
        rows.append(
            {
                "game_id": bet.game_id,
                "player": bet.player,
                "market": bet.market,
                "side": bet.side,
                "point": bet.point,
                "book": bet.book,
                "price": bet.price,
                "stake": bet.stake,
                "p_model": round(bet.p_model, 4),
                "p_fair": None if bet.p_fair is None else round(bet.p_fair, 4),
                "edge_score": round(c.edge_score, 4),
                "context_score": round(c.context_score, 4),
                "conviction": round(c.score, 4),
                "tier": c.tier,
                "recommended": not c.vetoed,
                "rationale": c.rationale(),
            }
        )
    columns = [
        "game_id", "player", "market", "side", "point", "book", "price", "stake",
        "p_model", "p_fair", "edge_score", "context_score", "conviction", "tier",
        "recommended", "rationale",
    ]
    return pd.DataFrame(rows, columns=columns)


def render_pick_sets(sets: Sequence[PickSet], *, heading: str = "Intelligence card") -> str:
    """The console block: each non-empty set with its argued picks."""
    lines = [f"=== {heading} ==="]
    any_pick = False
    for pick_set in sets:
        if not pick_set.picks:
            continue
        any_pick = True
        lines.append(f"\n{pick_set.label} ({len(pick_set.picks)}):")
        for c in pick_set.picks:
            marker = "✗" if c.vetoed else "•"
            lines.append(
                f"  {marker} {_bet_label(c)} — conviction {c.score:.2f} "
                f"[edge {c.edge_score:.2f}, context {c.context_score:+.2f}]"
            )
            lines.append(f"      {c.rationale()}")
    if not any_pick:
        lines.append("no assessed bets")
    return "\n".join(lines)
