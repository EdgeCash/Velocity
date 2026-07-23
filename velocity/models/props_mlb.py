"""MLB player props — priced straight off the Monte Carlo sample arrays.

Unlike the football props (a volume × share × efficiency decomposition), MLB
props need no separate model: the game sim already produced, per simulation, each
batter's total bases / hits / home runs / strikeouts and each starter's
strikeouts / outs. Pricing an over/under is then just reading the empirical
distribution of the relevant array — exactly the plan's "distributions straight
out of the sim."

Because every array comes from the same correlated game simulation, a batter's
props, his teammates', and the opposing pitcher's strikeout prop all move
together. Scratching a batter and re-simulating (:func:`substitute`) therefore
reprices the whole game — the pitcher faces a different lineup and the remaining
batters' plate-appearance counts shift — which is what makes injury repricing
honest.

Realistic pitcher counting props require the starter workload cap
(``BaseballSimConfig.starter_outs``); priced over a complete game the strikeout
and out numbers are inflated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from velocity.models.simulate_baseball import BaseballSimResult, Batter, Team

# Prop stat → the :class:`BaseballSimResult` attribute holding its sample arrays.
_BATTER_STATS = {
    "total_bases": "batter_total_bases",
    "hits": "batter_hits",
    "home_runs": "batter_home_runs",
    "strikeouts": "batter_strikeouts",
}
_PITCHER_STATS = {
    "pitcher_strikeouts": "pitcher_strikeouts",
    "pitcher_outs": "pitcher_outs",
}
_STAT_ATTR = {**_BATTER_STATS, **_PITCHER_STATS}


@dataclass(frozen=True)
class BaseballProps:
    """Empirical prop pricing over a :class:`BaseballSimResult`."""

    result: BaseballSimResult

    def _samples(self, player_id: str, stat: str) -> np.ndarray:
        attr = _STAT_ATTR.get(stat)
        if attr is None:
            raise ValueError(f"unknown prop stat {stat!r}")
        table: Mapping[str, np.ndarray] = getattr(self.result, attr)
        if player_id not in table:
            raise KeyError(f"{player_id!r} has no {stat} samples")
        return table[player_id]

    def mean(self, player_id: str, stat: str) -> float:
        return float(np.mean(self._samples(player_id, stat)))

    def prob_over(self, player_id: str, stat: str, line: float) -> float:
        """Empirical P(stat > line). Pushes (== line) are excluded, as books grade them."""
        return float(np.mean(self._samples(player_id, stat) > line))

    def prob_under(self, player_id: str, stat: str, line: float) -> float:
        """Empirical P(stat < line)."""
        return float(np.mean(self._samples(player_id, stat) < line))

    def prob_push(self, player_id: str, stat: str, line: float) -> float:
        """Empirical P(stat == line) — nonzero only on a whole-number line."""
        return float(np.mean(self._samples(player_id, stat) == line))

    def distribution(self, player_id: str, stat: str) -> dict[int, float]:
        """The full empirical pmf ``{value: probability}``; sums to 1."""
        samples = self._samples(player_id, stat)
        values, counts = np.unique(samples, return_counts=True)
        n = int(samples.shape[0])
        return {int(v): float(c) / n for v, c in zip(values, counts, strict=False)}


def substitute(team: Team, out_player_id: str, replacement: Batter) -> Team:
    """Return ``team`` with ``out_player_id`` swapped for ``replacement`` in the order.

    Re-projecting after the swap reprices the whole game: the opposing pitcher
    faces a different lineup (his strikeout prop shifts) and the remaining batters'
    plate-appearance counts move as the order changes.
    """
    lineup = list(team.lineup)
    for i, batter in enumerate(lineup):
        if batter.player_id == out_player_id:
            lineup[i] = replacement
            return Team(lineup=lineup, pitcher=team.pitcher)
    raise KeyError(f"{out_player_id!r} not in lineup")
