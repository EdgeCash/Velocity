"""Sports that cannot end level play on until they are decided.

Baseball's sim needed rebuilding from the ground up (``velocity/models/
counts.py``): runs are small integers, the distribution is overdispersed
against Poisson, and the home team's unbatted ninth makes the whole thing
asymmetric. **Basketball needs none of that**, and porting it would have been
pattern-matching rather than modelling — a WNBA score is eighty-odd points,
where a normal is a perfectly good approximation, and both teams play all
forty minutes whatever the score.

What the two sports do share is one thing: **the final score cannot be level**.
A rounded normal on the margin says otherwise. At the WNBA's own dispersion it
puts about 3% of its probability on a tie, and there are **zero** ties in the
875 banked games, because basketball plays an extra period.

That 3% is worth less than it looks on the moneyline — ``p_home_win`` splits
ties evenly, and an extra period is close to a coin flip, so the split is
nearly right by accident. It is worth a great deal on two other things:

* **the short spreads.** With a lump of mass at exactly zero, covering −0.5
  and covering +0.5 differ by that whole 3%, when in a sport with no ties they
  should differ by almost nothing.
* **the total.** An extra period adds points. In the banked games the ones
  decided by three or fewer carry a mean total of 170.5 against 164.8 for
  those decided by thirteen or more — a six-point gradient a tie-scoring sim
  cannot produce, because it ends those games early.

So this is deliberately small: when the rounded scores come out level, play
the extra period the real game would have played. Pure, and deterministic
under the caller's generator.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# At most this many extra periods before a sample is forced to a decision.
# Real games have gone to four; the bound is there so a degenerate config can
# never spin, not because a fifth is impossible.
MAX_PERIODS = 6


@dataclass(frozen=True)
class OvertimeConfig:
    """What one extra period adds, for a sport that cannot end level.

    ``points`` is the COMBINED points an extra period puts on the board and
    ``margin_sd`` the dispersion of its own margin — a five-minute WNBA period
    at the league's ~2.1 points per team per minute is about twenty-one
    combined, decided by a handful. ``home_edge`` defaults to zero: an extra
    period is close enough to a coin flip, and with only final scores banked
    there is no way to tell which games went to one, so there is nothing to
    fit a home edge against.
    """

    points: float
    points_sd: float
    margin_sd: float
    home_edge: float = 0.0

    def __post_init__(self) -> None:
        if self.points < 0 or self.points_sd < 0:
            raise ValueError("an extra period cannot remove points")
        if self.margin_sd <= 0:
            raise ValueError("margin_sd must be positive")


# A WNBA overtime is five minutes against regulation's forty. The league
# scores ~84 points a team in regulation, so a period is ~10.5 a side and ~21
# combined; the margin dispersion is regulation's scaled by the time (12.9 ×
# √(5/40) ≈ 4.6). Fitted no further than that: with only final scores banked,
# which games went to overtime is not observable, so these are derived from
# the clock and then CHECKED against the joint distribution they produce
# (docs/BUILD_WNBA_SIM.md).
WNBA_OVERTIME = OvertimeConfig(points=21.0, points_sd=6.0, margin_sd=4.6)


def resolve_ties(
    home: np.ndarray, away: np.ndarray, rng: np.random.Generator,
    config: OvertimeConfig,
) -> tuple[np.ndarray, np.ndarray]:
    """Play out every level sample. Returns the decided ``(home, away)``.

    Both sides score in the extra period — which is what separates this from
    baseball's, where the tie mass resolves by adding a run to one team — so
    the total rises as well as the margin moving off zero.
    """
    home = home.astype(float).copy()
    away = away.astype(float).copy()
    for _period in range(MAX_PERIODS):
        tied = home == away
        n = int(np.count_nonzero(tied))
        if not n:
            break
        points = np.maximum(
            rng.normal(config.points, config.points_sd, size=n), 0.0)
        margin = rng.normal(config.home_edge, config.margin_sd, size=n)
        extra_home = np.rint((points + margin) / 2.0)
        extra_away = np.rint((points - margin) / 2.0)
        home[tied] += np.maximum(extra_home, 0.0)
        away[tied] += np.maximum(extra_away, 0.0)
    # A sample still level after the bound is decided rather than returned as
    # a tie: the one thing this module exists to rule out. Both sides have to
    # be touched — giving the point to the home team on a coin flip and to
    # nobody on the other face leaves half of them level, which is how this
    # was first written and what the test caught.
    still_tied = home == away
    if still_tied.any():
        n = int(np.count_nonzero(still_tied))
        home_wins = rng.random(n) < 0.5
        home[still_tied] += home_wins
        away[still_tied] += ~home_wins
    return home, away
