"""The low-scoring count sim — for sports whose score is a small integer.

The shared Monte Carlo (:mod:`velocity.models.simulate`) draws a game's margin
and total from a bivariate normal and rounds. For football that is a good
first-order treatment. For baseball it is wrong in three ways at once, and all
three cost money on the markets this system actually trades:

* **It scores ties.** At the shipped MLB dispersion the normal puts 13.5% of
  its probability on a level final score. Baseball has none: zero ties in the
  7,149 games banked in ``datasets/mlb``. That mass has to go somewhere, and
  where it goes is the whole moneyline.
* **It is under-dispersed.** The shipped ``sd_margin`` of 3.2 against a
  walk-forward residual sd of **4.53** (n=4,695) prices baseball as a third
  more predictable than the model actually is, compressing every alternate
  line and every exchange ladder rung toward the middle.
* **It is symmetric, and baseball is not.** The home team does not bat in the
  bottom of the ninth when it already leads, and a walk-off ends the instant
  the lead is taken. So a home team's runs are *censored by winning*: it wins
  by exactly one in 32.7% of its wins against the away team's 23.1%, and its
  run total has a visibly smaller spread (sd 3.11 vs 3.26) despite scoring
  slightly more. A normal on the margin cannot represent that at all.

So this module models the thing itself. Each side's runs are a negative
binomial count — real team-games are overdispersed against Poisson (mean 4.46,
variance 9.65) — the home team's ninth inning is dropped when it is already
ahead, a lead taken in the ninth ends the rally where it stands, and a game
level after nine goes to extra innings, where it cannot stay level.

The constants were fitted on 2024–25 and **validated on the 2,200 held-out
2026 games**: the simulated home win rate lands at 0.5285 against an actual
0.5277, and the whole signed-margin profile tracks bucket by bucket. See
``docs/BUILD_MLB.md``.

Pure and deterministic under the caller's generator, like the normal path.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CountSimConfig:
    """How a low-scoring count sport turns two expected scores into a game.

    ``dispersion`` is the negative binomial's ``k``: variance is
    ``mu + mu**2 / k``, so a smaller number means a longer tail. The rest
    describe the endgame, which is where the asymmetry lives.
    """

    dispersion: float
    # The home team's expected runs arrive on the CENSORED scale — the ratings
    # are fitted on final scores, which already omit the ninth innings it never
    # batted. This inflates them back to what it would have scored batting a
    # full nine, so the censoring below has something to remove.
    home_uncensored: float = 1.0
    # A lead taken in the last inning ends the rally, but the run-scoring play
    # itself can clear the bases: the share of the would-be surplus that still
    # crosses the plate.
    walkoff_spill: float = 0.3
    # Extra innings: how often the home team wins one, and how heavy the tail
    # on the winning margin is (the free runner makes a two- or three-run half
    # ordinary). 0 means every extra-inning game ends by exactly one.
    extra_home_win: float = 0.55
    extra_margin_tail: float = 0.35
    innings: int = 9

    def __post_init__(self) -> None:
        if self.dispersion <= 0:
            raise ValueError("dispersion must be positive")
        if self.home_uncensored < 1.0:
            raise ValueError("home_uncensored inflates a censored mean; it cannot shrink one")
        if not 0.0 <= self.walkoff_spill <= 1.0:
            raise ValueError("walkoff_spill is a share, so it lives in [0, 1]")
        if not 0.0 <= self.extra_home_win <= 1.0:
            raise ValueError("extra_home_win is a probability")
        if not 0.0 <= self.extra_margin_tail < 1.0:
            raise ValueError("extra_margin_tail must be in [0, 1)")
        if self.innings < 1:
            raise ValueError("a game needs at least one inning")


# Fitted on the 4,949 banked 2024–25 games and validated on the 2,200 held-out
# 2026 ones (docs/BUILD_MLB.md). The dispersion reproduces the per-side run
# variance, the inflation reproduces the ~0.22 runs a season of unbatted ninth
# innings removes, and the endgame terms reproduce the home/away split of
# one-run games that the censoring creates.
MLB_COUNTS = CountSimConfig(
    dispersion=3.25,
    home_uncensored=1.05,
    walkoff_spill=0.3,
    extra_home_win=0.55,
    extra_margin_tail=0.35,
)


def simulate_counts(
    mu_home: float,
    mu_away: float,
    rng: np.random.Generator,
    config: CountSimConfig,
    n_sims: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample ``n_sims`` final scores as counts. Returns ``(home, away)``.

    No sample ends level: a game tied after regulation is played out, which is
    what makes the moneyline and the one-run markets honest.
    """
    if n_sims <= 0:
        raise ValueError("n_sims must be positive")
    # A rating can project a non-positive expectation on an extreme matchup;
    # the count distribution needs a positive mean, and zero runs is already
    # the floor the distribution itself supplies.
    lo = 1e-3
    mu_a = max(float(mu_away), lo)
    mu_h = max(float(mu_home), lo) * config.home_uncensored
    k = config.dispersion

    def draw(mu: float) -> np.ndarray:
        return rng.negative_binomial(k, k / (k + mu), size=n_sims)

    away = draw(mu_a)
    # The home team's full nine innings, then the split into "before its last
    # inning" and "its last inning" — thinning, which is exact for a Poisson
    # process and close enough for the overdispersed counts real teams produce.
    home_full = draw(mu_h)
    share = (config.innings - 1) / config.innings
    home_early = rng.binomial(home_full, share)
    home_last = home_full - home_early
    home = home_full.copy()

    # 1. Already ahead going into its last at-bat: it never takes one.
    ahead = home_early > away
    home[ahead] = home_early[ahead]

    # 2. Takes the lead in its last at-bat: the rally stops there, except for
    #    whatever crossed on the play that did it.
    bats = ~ahead
    walkoff = bats & (home_early + home_last > away)
    surplus = np.maximum(home_early[walkoff] + home_last[walkoff] - away[walkoff] - 1, 0)
    home[walkoff] = away[walkoff] + 1 + rng.binomial(surplus, config.walkoff_spill)

    # 3. Level after regulation: extra innings, which cannot end level either.
    tied = bats & (home_early + home_last == away)
    n_tied = int(tied.sum())
    if n_tied:
        home_won = rng.random(n_tied) < config.extra_home_win
        by = rng.geometric(1.0 - config.extra_margin_tail, size=n_tied)
        home[tied] = away[tied] + np.where(home_won, by, 0)
        away[tied] = away[tied] + np.where(home_won, 0, by)

    return home.astype(float), away.astype(float)
