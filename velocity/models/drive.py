"""A drive-level score simulation: points arrive in sevens and threes.

The shipped simulator draws a game's margin and total from a bivariate normal
and rounds them (:mod:`velocity.models.simulate`). Its own docstring names the
weakness: that is "a first-order treatment of the well-known mass at key
numbers 3 and 7". A rounded normal puts a margin of 3 and a margin of 4 at
almost the same probability, and football does not — since 2015 the NFL has
landed on 3 in 14.8% of games and on 4 in 4.6%.

This samples the other way round. A team gets a number of possessions, each
possession ends in a touchdown, a field goal or nothing, and the score is
whatever that adds up to. Scores then live on the lattice real scores live on,
and the mass at 3, 7, 10 and 14 is a consequence of the structure rather than
a correction applied to it.

**The dispersion is not a knob here, and that is the interesting part.** With
football-realistic possession counts and scoring rates the implied margin
standard deviation lands within half a point of the constant the lab measured
on the walk-forward, which is a check on the structure rather than a fit to
it. Three mechanisms that a sampler of independent drives would otherwise
miss are modelled explicitly, because each leaves its own signature in the
data that the structure alone gets wrong:

* **Overtime** (:func:`_resolve_overtime`), without which the sim scores 4.3%
  ties against the league's 0.33%.
* **A shared scoring environment** (``pace_sd``), without which the simulated
  margin and total are equally dispersed. Real football's are not: the NFL
  walk-forward residual sd is 13.01 on the margin and 13.54 on the total, and
  that gap is exactly what two positively correlated team scores produce.
* **Projection error** (``strength_sd``), without which the sim claims the
  only thing it does not know about a game is how the possessions fall. That
  is nearly true in the NFL and badly false in college, whose residual margin
  sd of 16.2 is wider than any possession lattice can reach.

**Nothing in this module is wired into the live slate, and it has been
measured.** The gate's verdict is in ``docs/MODEL_LAB.md`` ("the drive
round") and it is split: against college it beats the shipped sim on
calibration, Brier, key-number mass and every totals column; against the NFL
it wins the key numbers and loses the spread profile, because eleven
independent possessions turn out to be *more* dispersed than NFL football is
— real games compress, and this does not model that. So neither variant is
promoted. Importing this module from anything but ``scripts/sim_lab.py``
means promoting it, which is its own decision with its own verification.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from velocity.models.simulate import GameSim

# A touchdown is six points and then a kick, and the kick is modelled rather
# than averaged into the six. Carrying a 6.94-point touchdown and rounding
# the team's total at the end biased the mean total up by about a quarter of
# a point — small, and pure noise added to the one number a totals model
# exists to get right. Sampling the extra point instead lands every score on
# the integer lattice with nothing to round.
TOUCHDOWN_POINTS = 6.0
EXTRA_POINT_RATE = 0.94
FIELD_GOAL_POINTS = 3.0
# What a touchdown drive is worth on average, which is what the scoring-rate
# solve below has to divide into.
TOUCHDOWN_VALUE = TOUCHDOWN_POINTS + EXTRA_POINT_RATE


@dataclass(frozen=True)
class DriveConfig:
    """Possessions per team and the shape of what they produce.

    ``drives`` is per team, not per game. ``fg_to_td`` is the ratio of
    field-goal drives to touchdown drives, held fixed while the scoring rate
    moves with the matchup: a better offense scores touchdowns AND kicks more
    field goals, rather than converting field goals into touchdowns at a
    constant drive count.

    Neither of those is a dispersion knob, and neither is fitted: both are
    countable facts about the sport. The two fitted parameters are the
    mechanisms the possession lattice does NOT contain, and they are
    separated because they leave different signatures:

    * ``strength_sd`` — the model not knowing the true teams, as a per-game
      shift applied one way to the home μ and the other way to the away μ.
      It widens the MARGIN and leaves the total alone.
    * ``pace_sd`` — the shared scoring environment (weather, tempo, game
      script), as a per-game multiplier on both μ. It widens the TOTAL and
      leaves the margin alone.

    ``overtime_rounds`` is how many paired extra possessions a level game
    plays before it is allowed to stand as a tie — see
    :func:`_resolve_overtime`.
    """

    drives: float = 11.0
    fg_to_td: float = 0.65
    strength_sd: float = 0.0
    pace_sd: float = 0.0
    overtime_rounds: int = 3
    n_sims: int = 50_000

    def __post_init__(self) -> None:
        if self.drives <= 0:
            raise ValueError("drives must be positive")
        if self.fg_to_td < 0:
            raise ValueError("fg_to_td cannot be negative")
        if self.pace_sd < 0:
            raise ValueError("pace_sd cannot be negative")
        if self.strength_sd < 0:
            raise ValueError("strength_sd cannot be negative")
        if self.overtime_rounds < 0:
            raise ValueError("overtime_rounds cannot be negative")
        if self.n_sims <= 0:
            raise ValueError("n_sims must be positive")


def drive_probabilities(
    mu_points: float | np.ndarray, config: DriveConfig
) -> tuple[np.ndarray, np.ndarray]:
    """``(p_touchdown, p_field_goal)`` per drive for a team expected to score ``mu_points``.

    Solved rather than assumed: with the field-goal-to-touchdown ratio held
    at ``fg_to_td``, the expected points per drive pins the touchdown rate,

        ppd = TD·p_td + FG·(k·p_td)   ⟹   p_td = ppd / (TD + FG·k)

    and the field-goal rate follows. A matchup so lopsided that the implied
    rates exceed one is clamped, which costs the extreme tail a little
    expectation and is far better than sampling a probability above one.

    Accepts an array so every sample can carry its own scoring environment.
    """
    per_drive = np.maximum(np.asarray(mu_points, dtype=float), 0.0) / config.drives
    denominator = TOUCHDOWN_VALUE + FIELD_GOAL_POINTS * config.fg_to_td
    p_td = per_drive / denominator
    p_fg = config.fg_to_td * p_td
    over = p_td + p_fg
    scale = np.where(over > 1.0, 1.0 / np.maximum(over, 1e-12), 1.0)
    return p_td * scale, p_fg * scale


def implied_score_sd(mu_points: float, config: DriveConfig) -> float:
    """The per-team scoring standard deviation this configuration implies.

    Closed form for the multinomial the sampler draws, so the structure can
    be checked against the lab's measured dispersion without running it.

    With ``strength_sd`` and ``pace_sd`` at zero this is the pure lattice —
    the ``V`` of :func:`fit_drive_config` — and exact to three decimals
    against the sampler. The two spread terms are added first-order (they
    carry each mechanism's own variance but not its effect on the per-drive
    variance), and a fractional ``drives`` is treated as a drive count
    rather than a mixture. Overtime is not in it at all: this is the
    regulation figure, and overtime adds about 1% on top.
    """
    p_td_a, p_fg_a = drive_probabilities(mu_points, config)
    p_td, p_fg = float(p_td_a), float(p_fg_a)
    td, fg = TOUCHDOWN_VALUE, FIELD_GOAL_POINTS
    variance = config.drives * (
        td * td * p_td * (1.0 - p_td)
        + fg * fg * p_fg * (1.0 - p_fg)
        - 2.0 * td * fg * p_td * p_fg
    )
    # The extra-point kick is its own coin on every touchdown drive.
    variance += config.drives * p_td * EXTRA_POINT_RATE * (1.0 - EXTRA_POINT_RATE)
    # Both spread mechanisms move the whole expectation, so each enters as
    # the variance of a shifted or multiplied mean.
    variance += (config.pace_sd * float(mu_points)) ** 2
    variance += config.strength_sd**2
    return float(np.sqrt(max(variance, 0.0)))


def fit_drive_config(
    resid_margin: np.ndarray, resid_total: np.ndarray, mu_total: np.ndarray,
    base: DriveConfig,
) -> DriveConfig:
    """``base`` with its two spread parameters fitted to banked residuals.

    Possessions do not explain all of a football game's unpredictability,
    and a sim that pretends they do is wrong in a way that is easy to miss.
    Two other things are going on, and each has its own arithmetic
    signature, which is what makes them separately identifiable from final
    scores alone. Writing ``V`` for the per-team variance the possession
    lattice itself produces:

        Var(margin) = 2V + 4·strength_sd²
        Var(total)  = 2V + μ_total²·pace_sd²

    The margin residuals therefore pin the projection error and the total
    residuals pin the scoring environment, each independently of the other
    and neither touching ``V``. Both are floored at zero: a league whose
    residuals are NARROWER than its own possession lattice is telling you
    the structure is wrong, not that the parameter is negative.

    **``fg_to_td`` is deliberately not fitted, and that is the lesson of the
    college run.** An earlier version used it as the dispersion control —
    a coarser lattice is a wider team score — and solved it against the
    margin residuals. In the NFL that produced football-plausible ratios
    near 0.8. In college, whose residual margin sd of 16.2 is wider than
    any possession lattice can reach, it ran to the bound and returned a
    lattice with NO FIELD GOALS IN IT: the fitted sim put 24% of its mass on
    a margin of exactly 7 and 0.02% on a margin of 3. It matched the
    dispersion and destroyed the one thing the drive sim exists for. The
    dispersion a lattice cannot reach is not the lattice's to supply.
    """
    var_margin = float(np.var(resid_margin))
    var_total = float(np.var(resid_total))
    anchor = float(np.mean(mu_total))
    lattice = implied_score_sd(
        anchor / 2.0, replace(base, strength_sd=0.0, pace_sd=0.0))
    unexplained_margin = max(var_margin - 2.0 * lattice**2, 0.0)
    unexplained_total = max(var_total - 2.0 * lattice**2, 0.0)
    strength = float(np.sqrt(unexplained_margin / 4.0))
    pace = float(np.sqrt(unexplained_total) / anchor) if anchor > 0 else 0.0
    return replace(base, strength_sd=strength, pace_sd=pace)


def _possessions(
    p_td: np.ndarray, p_fg: np.ndarray, n_possessions: np.ndarray | int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Points from ``n_possessions`` drives, per sample.

    Drawn as two sequential binomials rather than one multinomial — the same
    distribution, but ``rng.binomial`` vectorizes over a per-sample
    probability and ``rng.multinomial`` does not, which is what lets every
    sample carry its own scoring environment.

    Every touchdown takes its own kick, so a score is an integer by
    construction and nothing is rounded.
    """
    touchdowns = rng.binomial(n_possessions, np.clip(p_td, 0.0, 1.0))
    # Field goals come from the drives that were not touchdowns, at the rate
    # conditional on that, which is what makes the pair multinomial.
    conditional = np.where(p_td < 1.0, p_fg / np.maximum(1.0 - p_td, 1e-12), 0.0)
    field_goals = rng.binomial(
        np.asarray(n_possessions) - touchdowns, np.clip(conditional, 0.0, 1.0))
    extra_points = rng.binomial(touchdowns, EXTRA_POINT_RATE)
    return (touchdowns * TOUCHDOWN_POINTS + extra_points
            + field_goals * FIELD_GOAL_POINTS).astype(float)


def _team_scores(
    mu_points: np.ndarray, config: DriveConfig, rng: np.random.Generator
) -> np.ndarray:
    p_td, p_fg = drive_probabilities(mu_points, config)
    floor = int(np.floor(config.drives))
    extra = float(config.drives - floor)
    scores = _possessions(p_td, p_fg, floor, rng)
    if extra > 0:
        # A fractional drive count is a league average, not a promise; the
        # extra possession is played with the probability of its fractional
        # part so an 11.4-drive league is not silently an 11-drive one.
        played = rng.random(config.n_sims) < extra
        scores = scores + played * _possessions(p_td, p_fg, 1, rng)
    return scores


def _resolve_overtime(
    home: np.ndarray, away: np.ndarray, mu_home: np.ndarray, mu_away: np.ndarray,
    config: DriveConfig, rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Play out level games — but let a few of them stay level.

    This is the one place football differs from every other sport the repo
    simulates. Basketball and baseball **cannot** end tied, so
    :mod:`velocity.models.overtime` forces every level sample to a decision.
    Football can: ten of the 3,045 NFL games since 2015 ended level, 0.33%.
    Forcing a decision here would be as wrong as the 4.3% of ties the sim
    scored before there was an overtime at all.

    A round is one possession each at the teams' own scoring rates, repeated
    while the game is level. That is not the rulebook — regular-season
    overtime is sudden death on a touchdown, so the second possession is
    often never played — and modelling the rulebook would need possession
    ORDER, which a sim that samples two independent team totals does not
    have. So the round count is **calibrated to the tie rate instead**: three
    rounds leaves 0.4% of games level, which is the league's own figure. It
    earns its place by reproducing the observable, not by narrating the rule.
    """
    if config.overtime_rounds <= 0:
        return home, away
    home = home.copy()
    away = away.copy()
    p_home = drive_probabilities(mu_home, config)
    p_away = drive_probabilities(mu_away, config)
    for _round in range(config.overtime_rounds):
        tied = home == away
        if not tied.any():
            break
        home[tied] += _possessions(p_home[0][tied], p_home[1][tied], 1, rng)
        away[tied] += _possessions(p_away[0][tied], p_away[1][tied], 1, rng)
    return home, away


def simulate_drives(
    mu_margin: float,
    mu_total: float,
    rng: np.random.Generator,
    config: DriveConfig | None = None,
) -> GameSim:
    """A game sampled as possessions, returned in the shipped sim's shape.

    Same inputs and same output type as
    :func:`velocity.models.simulate.simulate_game`, so every pricing helper
    reads it unchanged and the two can be compared on identical projections.
    """
    cfg = config or DriveConfig()
    mu_home = (float(mu_total) + float(mu_margin)) / 2.0
    mu_away = (float(mu_total) - float(mu_margin)) / 2.0
    # One scoring environment per sample, shared by both teams: the same
    # weather, pace and game script move both scores together. Floored well
    # clear of zero — at a realistic spread the floor is a dozen standard
    # deviations away and never binds, but a caller is free to pass a wild
    # one and must not get negative expected points for it.
    if cfg.pace_sd > 0:
        pace = np.maximum(rng.normal(1.0, cfg.pace_sd, size=cfg.n_sims), 0.0)
    else:
        pace = np.ones(cfg.n_sims)
    # And one projection error per sample, applied in OPPOSITE directions:
    # the model being wrong about who is better moves the margin without
    # moving the total, which is the signature that separates it from pace.
    if cfg.strength_sd > 0:
        shift = rng.normal(0.0, cfg.strength_sd, size=cfg.n_sims)
    else:
        shift = np.zeros(cfg.n_sims)
    mu_h = np.maximum(mu_home * pace + shift, 0.0)
    mu_a = np.maximum(mu_away * pace - shift, 0.0)
    home = _team_scores(mu_h, cfg, rng)
    away = _team_scores(mu_a, cfg, rng)
    home, away = _resolve_overtime(home, away, mu_h, mu_a, cfg, rng)
    return GameSim(home_score=home, away_score=away)
