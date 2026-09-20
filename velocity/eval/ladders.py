"""Is the sim honest at a given ladder rung? — the E8 calibration gate.

An exchange lists a whole ladder per game: ~25 spread rungs and ~25 total
rungs, each its own binary contract. The sim can price every one of them
(``model_probability`` accepts any point), but *can* is not *should*, so this
measures how far the sim's own tail probabilities sit from the games' and
refuses the rungs where the gap swamps the edge being claimed.

**The reference is the sim itself.** That sounds obvious and was not true
until the derivative re-check (docs/MODEL_LAB.md): the table used to compare
the games against a CONTINUOUS NORMAL fitted to the residuals, as a stand-in
for the sim. The stand-in was wrong in the expensive direction. The real sim
rounds, and so does football — 52% of NFL closing spreads are whole numbers,
which makes those games' residuals integers, so the empirical tail past a
half-point offset is discrete and a rounded sim reproduces what a continuous
one cannot. The stand-in charged the sim about 0.008 of probability it never
spent, half the default tolerance, and that closed **fourteen ladder sides**
across the two leagues: NFL spreads went from 41 of 58 open to 50, NCAAF
totals from 48 to 53. :func:`simulated_tails` now asks the sim directly.

Measured that way, against the market's own closing number — the sharpest
per-game expectation available, so this isolates the shape of outcomes rather
than the model's aim — two different things are wrong, one per market.

**Spreads: the sim was too fat in the shoulders, and the lattice fixed it.**
Real spread residuals are leptokurtic (more mass near zero, thinner
shoulders) and close to symmetric — skew +0.10 in the NFL, +0.01 in college.
A normal matched on dispersion therefore overstates both tails at once,
worst in the shoulders: NFL spreads peaked at 2.9 points of probability
around 4.5 out, past the two-point edge the slate bets on, so they were
refused near the line. The promotion round (docs/MODEL_LAB.md) found that
this was never dispersion: it was the normal putting mass on 9, 11, 12 and
15 that football puts on 3, 7 and 14. Both sims now resample their draws by
football's own margin lattice (``SimConfig.lattice``, banked in
``datasets/{league}/lattice.parquet``): NFL spreads read 1.4 at the same
shoulder and every side is open (50 → 58 of 58); NCAAF spreads, already
inside the bar at 1.7, read 1.5. The tail round is why college could take
it: with the table's tail bin corrected — a dispersion ratio, not a lattice
one, over a third of college margins — college closed five spread sides;
with the tail left at 1, as the bank now does, it opens them all. The
college totals shoulder sits on the bar either way (2.5, 4.5 and 6.5 over
read 0.0198–0.0200 without the lattice and 0.0202–0.0205 with it, by parity:
resampling toward odd margins moves totals toward odd numbers), so three
totals sides close by a few ten-thousandths.

**Totals: the sim is symmetric and football is not.** Total residuals are
right-skewed in both leagues — +0.33 in the NFL, +0.34 in college — because a
game can run away upward and cannot run away downward. The sim is symmetric,
so near the line it puts too much in the OVER tail and too little in the
under: at 4.5 out, NFL totals are +0.028 over and −0.008 under, NCAAF +0.020
and −0.020. This is a different defect from the spreads' and wants a
different fix; a fatter-tailed draw would not touch it.

A draw that CAN express it exists — :mod:`velocity.models.skew`, reachable
from ``SimConfig.total_skew`` — and measured here it roughly halves the
totals error and opens every totals rung in both leagues (49/58 → 58/58 for
the NFL, 53/58 → 58/58 for college). It is not shipped, and the reason is
recorded in ``docs/MODEL_LAB.md`` under "the skew round": measured around the
model's own μ rather than around a level-matched close, the same parameter
helps college and hurts the NFL, because each league's totals level wanders
by more than the shape is wrong. Until that level is addressed these nine and
five sides stay shut.

**The two tails do not move together, and the gate reads them apart.**
:data:`OFFSET_BIAS` keeps the error *signed* per tail — positive where the sim
overstates that tail — because only one sign is dangerous. A rung whose own
side the sim **overstates** is one the sim wants to buy for a reason that is
not there; a rung whose side it **understates** cannot have its edge invented
by this bias, only hidden. The split is large and it is where the skew shows:
on NFL spreads the favourite's tail turns negative past 15.5 while the dog's
stays positive out to 22.5, and on NFL totals the under tail is the overstated
one from 9.5 out — the only one needing a gate there. The old symmetric
``max(|over|, |under|)`` charged every rung the worse tail's error, which both
blocked near-the-line rungs the bias could only help and said nothing about
which deep rung was the trap.

**The deep tail is measured, not assumed innocent.** An earlier version stopped
at 20.5 and let anything past it through on the argument that the error out
there "was already small and shrinking", and the first live exchange board bet
straight into that unexamined region — every qualifying rung on it was 15 to 22
points out, because the gate had blocked everything nearer the line. NFL totals
still carry 1.3 points of probability at 20.5, two-thirds of the tolerance,
declining to 0.6 by 28.5. So the tables run to 28.5 and a rung past their end is
refused rather than assumed innocent.

**And the absolute bar is in the wrong units for the deep tail.** It compares a
probability error against a tolerance in ``min_edge``'s units, which is right
for the qualifying test — a shape error as large as the edge threshold makes
that edge meaningless. But a rung's *EV per unit staked* moves by the error
divided by its price, so the same 0.6-point miss that is negligible at even
money is a tenth of stake on a 6-cent contract. Measured on the committed
datasets that ratio does not shrink with distance, it **grows**. So the absolute
bar admitted precisely the rungs where a shape error costs the most, which is
why the first live exchange board came back all deep tail.
:func:`rung_is_honest` therefore charges the bar
``min(tolerance, relative_tolerance × price)`` — the same EV budget the
absolute bar already accepts at even money, held constant across the price
range, which is why the two tests agree exactly at 0.5 instead of being two
independent knobs.

Residuals here are measured against the market's close, so they describe
outcome shape given a sharp expectation. The sim's own residual is around
*its* projection, which is at best as sharp; if it is less sharp its residuals
are wider and this shape error is diluted. That makes the gate conservative,
not permissive — the right way to be wrong. The level is removed before
measuring (:func:`simulated_tails` shifts the sim by the empirical residual's
mean, as the fitted normal did by taking it), so this is a statement about
shape and never about the close being a tenth of a point off.

The fix at the source was tried (docs/MODEL_LAB.md, the sim-shape round): the
sim can draw from the model's own banked residual pairs instead of a normal
(:mod:`velocity.models.residuals`). Measured around the model's μ it trims
the spread shoulder error by a tenth and costs moneyline calibration, so it
is not the default and this gate stays. What that round did remove was a
level bias worth two-thirds of the totals error — which no shape gate could
have seen.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from velocity.models.simulate import SimConfig

# Signed probability error at each half-point offset from the fair line, as
# ``(over_bias, under_bias)`` per (league, market): the normal's tail
# probability minus the empirical one, so **positive means the sim overstates
# that tail**.
#
# EVERYTHING BELOW THIS COMMENT IS GENERATED. `scripts/calibrate_ladders.py
# --write` replaces the literal wholesale from the committed datasets, and the
# refresh job runs it whenever datasets/ moves — so any note written inside the
# block is deleted the next time the data grows. That already happened once:
# regenerating by hand silently dropped both of the notes now kept here.
#
# What the measurement is FOR, which the generator does not know:
#   * NFL spreads measure a residual sd of ~12.98 and the sim uses 13.0.
#   * NCAAF spreads measure ~15.5, and the sim deliberately uses 18.2 — wider
#     than the residual around the MARKET CLOSE, because the close is a
#     sharper expectation than the model's own and pretending otherwise would
#     hand the gate a bar it cannot clear.
OFFSET_BIAS: Mapping[tuple[str, str], Mapping[float, tuple[float, float]]] = {
    # n=4113 completed games, residual sd 12.98
    ("nfl", "spread"): {
        0.5: (+0.0070, +0.0008), 1.5: (+0.0094, +0.0032), 2.5: (+0.0093, +0.0047),
        3.5: (+0.0117, +0.0058), 4.5: (+0.0136, +0.0041), 5.5: (+0.0129, +0.0037),
        6.5: (+0.0116, +0.0079), 7.5: (+0.0114, +0.0085), 8.5: (+0.0071, +0.0074),
        9.5: (+0.0054, +0.0050), 10.5: (+0.0063, +0.0055), 11.5: (+0.0080, +0.0083),
        12.5: (+0.0028, +0.0065), 13.5: (+0.0005, +0.0060), 14.5: (-0.0011, +0.0036),
        15.5: (-0.0057, +0.0018), 16.5: (-0.0067, +0.0013), 17.5: (-0.0083, -0.0021),
        18.5: (-0.0092, +0.0001), 19.5: (-0.0114, +0.0002), 20.5: (-0.0102, +0.0014),
        21.5: (-0.0107, +0.0022), 22.5: (-0.0114, +0.0002), 23.5: (-0.0105, -0.0011),
        24.5: (-0.0092, -0.0017), 25.5: (-0.0075, -0.0029), 26.5: (-0.0078, -0.0042),
        27.5: (-0.0073, -0.0036), 28.5: (-0.0065, -0.0037),
    },
    # n=4113 completed games, residual sd 13.21
    ("nfl", "total"): {
        0.5: (+0.0231, -0.0248), 1.5: (+0.0237, -0.0235), 2.5: (+0.0238, -0.0187),
        3.5: (+0.0296, -0.0142), 4.5: (+0.0286, -0.0086), 5.5: (+0.0280, -0.0076),
        6.5: (+0.0286, -0.0082), 7.5: (+0.0258, -0.0040), 8.5: (+0.0203, -0.0031),
        9.5: (+0.0172, +0.0038), 10.5: (+0.0120, +0.0045), 11.5: (+0.0105, +0.0047),
        12.5: (+0.0116, +0.0094), 13.5: (+0.0109, +0.0113), 14.5: (+0.0093, +0.0152),
        15.5: (+0.0059, +0.0150), 16.5: (+0.0041, +0.0166), 17.5: (+0.0010, +0.0163),
        18.5: (-0.0003, +0.0151), 19.5: (-0.0026, +0.0145), 20.5: (-0.0019, +0.0131),
        21.5: (-0.0029, +0.0126), 22.5: (-0.0040, +0.0105), 23.5: (-0.0049, +0.0100),
        24.5: (-0.0074, +0.0091), 25.5: (-0.0069, +0.0092), 26.5: (-0.0069, +0.0080),
        27.5: (-0.0063, +0.0069), 28.5: (-0.0034, +0.0056),
    },
    # n=11701 completed games, residual sd 15.51
    ("ncaaf", "spread"): {
        0.5: (+0.0086, -0.0026), 1.5: (+0.0100, +0.0041), 2.5: (+0.0095, +0.0068),
        3.5: (+0.0085, +0.0098), 4.5: (+0.0092, +0.0125), 5.5: (+0.0099, +0.0122),
        6.5: (+0.0088, +0.0142), 7.5: (+0.0103, +0.0130), 8.5: (+0.0087, +0.0128),
        9.5: (+0.0086, +0.0132), 10.5: (+0.0089, +0.0150), 11.5: (+0.0092, +0.0131),
        12.5: (+0.0085, +0.0125), 13.5: (+0.0073, +0.0113), 14.5: (+0.0101, +0.0120),
        15.5: (+0.0070, +0.0122), 16.5: (+0.0075, +0.0109), 17.5: (+0.0079, +0.0095),
        18.5: (+0.0069, +0.0098), 19.5: (+0.0043, +0.0082), 20.5: (+0.0045, +0.0075),
        21.5: (+0.0035, +0.0076), 22.5: (+0.0032, +0.0072), 23.5: (+0.0027, +0.0072),
        24.5: (+0.0017, +0.0073), 25.5: (+0.0008, +0.0071), 26.5: (+0.0002, +0.0067),
        27.5: (+0.0011, +0.0051), 28.5: (+0.0000, +0.0046),
    },
    # n=11701 completed games, residual sd 16.21
    ("ncaaf", "total"): {
        0.5: (+0.0245, -0.0247), 1.5: (+0.0219, -0.0259), 2.5: (+0.0201, -0.0246),
        3.5: (+0.0197, -0.0204), 4.5: (+0.0202, -0.0200), 5.5: (+0.0222, -0.0202),
        6.5: (+0.0202, -0.0178), 7.5: (+0.0191, -0.0149), 8.5: (+0.0192, -0.0156),
        9.5: (+0.0174, -0.0158), 10.5: (+0.0135, -0.0154), 11.5: (+0.0105, -0.0129),
        12.5: (+0.0061, -0.0126), 13.5: (+0.0044, -0.0104), 14.5: (+0.0031, -0.0098),
        15.5: (+0.0038, -0.0081), 16.5: (+0.0041, -0.0069), 17.5: (+0.0011, -0.0062),
        18.5: (-0.0010, -0.0042), 19.5: (-0.0024, -0.0031), 20.5: (-0.0024, -0.0023),
        21.5: (-0.0022, -0.0010), 22.5: (-0.0035, -0.0006), 23.5: (-0.0030, +0.0011),
        24.5: (-0.0046, +0.0027), 25.5: (-0.0062, +0.0038), 26.5: (-0.0064, +0.0039),
        27.5: (-0.0061, +0.0031), 28.5: (-0.0058, +0.0027),
    },
}

# The symmetric view :func:`offset_error` answers with: the worse of the two
# tails, which is what a gate that does not know the side can charge. Derived
# rather than banked twice, so the two can never drift apart.
OFFSET_ERROR: Mapping[tuple[str, str], Mapping[float, float]] = {
    key: {offset: max(abs(over), abs(under)) for offset, (over, under) in table.items()}
    for key, table in OFFSET_BIAS.items()
}

# Team totals ride the game total's shape; props are priced by a different sim
# and are out of this gate's scope.
_MARKET_ALIASES = {
    "team_total_home": "total",
    "team_total_away": "total",
    "moneyline": None,  # no number, so no offset to be wrong about
}

# Default tolerance: the slate's default ``min_edge``. An error as large as the
# edge threshold makes the edge meaningless, so a rung is only bettable when
# the sim's shape error is smaller than the edge being claimed.
DEFAULT_TOLERANCE = 0.02


def _normal_sf(x: float, mu: float, sd: float) -> float:
    """P(X > x) for a normal — the sim's implied tail, without scipy."""
    return 0.5 * math.erfc((x - mu) / (sd * math.sqrt(2.0)))


def simulated_tails(
    games: pd.DataFrame, market: str, config: SimConfig, offsets: list[float],
    *, level: float = 0.0, seed: int = 20260920,
) -> tuple[list[float], list[float]]:
    """How often the SIM lands past each offset, averaged over these games.

    The reference the gate should have been using all along. Every game is
    simulated at the market's own numbers — the sharpest per-game expectation
    available, and the same choice the empirical side makes — and its draws
    counted past each offset.

    ``level`` shifts the simulated expectation by the empirical residual's
    mean, which keeps this a **shape** measurement rather than a level one.
    The fitted normal it replaces did the same thing by taking the residual's
    own mean; without it the gate would charge the sim for the market close
    being a tenth of a point off, which is not what it is for.

    Deterministic under ``seed``: a banked constant that moved between runs
    would fail its own freshness test for no reason.
    """
    from velocity.models.simulate import simulate_game
    from velocity.util.seed import make_rng

    column = "spread_line" if market == "spread" else "total_line"
    spreads = games["spread_line"].astype(float).to_numpy()
    totals = games["total_line"].astype(float).to_numpy()
    lines = games[column].astype(float).to_numpy()
    grid = np.asarray(offsets, dtype=float)
    over = np.zeros(grid.size)
    under = np.zeros(grid.size)
    for i, (spread, total, line) in enumerate(
            zip(spreads, totals, lines, strict=True)):
        mu_margin = float(spread) + (level if market == "spread" else 0.0)
        mu_total = float(total) + (level if market == "total" else 0.0)
        sim = simulate_game(mu_margin, mu_total, make_rng(seed + i), config)
        draw = np.sort(sim.margin if market == "spread" else sim.total)
        n = float(sim.n_sims)
        # One sort, then every offset's tail by bisection.
        over += 1.0 - np.searchsorted(draw, line + grid, side="right") / n
        under += np.searchsorted(draw, line - grid, side="left") / n
    return list(over / len(games)), list(under / len(games))


def residual_calibration(
    games: pd.DataFrame,
    market: str,
    *,
    max_offset: float = 20.5,
    reference: SimConfig | None = None,
) -> pd.DataFrame:
    """Measure how far the model misses at each half-point offset from the line.

    ``games`` needs final scores plus the market's closing number
    (``spread_line`` / ``total_line``). Returns one row per offset with the
    empirical and modelled tail probabilities on each side, the **signed**
    error per tail (``over_bias`` / ``under_bias``, positive where the model
    overstates that tail), the worst of the two as ``error``, and each tail's
    error as a fraction of the probability quoted there. :data:`OFFSET_BIAS` is
    generated from the signed columns; the relative ones are what
    :func:`rung_is_honest` scales its bar by, since a rung's EV moves by its
    error divided by its price.

    ``reference`` is **the model being gated**, and passing one is the point:
    with a :class:`~velocity.models.simulate.SimConfig` this simulates the
    real sim, rounding and all. Without one it falls back to a continuous
    normal fitted to the residuals, which is what the table used to be built
    from and is kept only so the two can be compared — see the module
    docstring on why that stand-in was costing twenty ladder sides.
    """
    column = "spread_line" if market == "spread" else "total_line"
    frame = games.dropna(
        subset=["home_score", "away_score", "spread_line", "total_line"])
    if frame.empty:
        return pd.DataFrame(columns=["offset", "empirical_over", "model_over", "error"])

    if market == "spread":
        outcome = frame["home_score"] - frame["away_score"]
    else:
        outcome = frame["home_score"] + frame["away_score"]
    residual = (outcome - frame[column]).astype(float).to_numpy()
    mu, sd = float(residual.mean()), float(residual.std(ddof=1))

    offsets = []
    offset = 0.5
    while offset <= max_offset:
        offsets.append(offset)
        offset += 1.0
    if reference is None:
        over_mod = [_normal_sf(o, mu, sd) for o in offsets]
        under_mod = [1.0 - _normal_sf(-o, mu, sd) for o in offsets]
    else:
        over_mod, under_mod = simulated_tails(
            frame, market, reference, offsets, level=mu)

    rows = []
    for i, off in enumerate(offsets):
        over_emp = float((residual > off).mean())
        under_emp = float((residual < -off).mean())
        over_nor, under_nor = over_mod[i], under_mod[i]
        rows.append(
            {
                "offset": off,
                "empirical_over": over_emp,
                "model_over": over_nor,
                "empirical_under": under_emp,
                "model_under": under_nor,
                # Signed, and in the dangerous direction when positive: the
                # model claims more mass past this threshold than the games
                # actually put there, so the sim wants to buy that tail.
                "over_bias": over_nor - over_emp,
                "under_bias": under_nor - under_emp,
                "error": max(abs(over_emp - over_nor), abs(under_emp - under_nor)),
                # The same miss as a fraction of the probability being quoted.
                # A rung's EV moves by (absolute error / price), so this is
                # what an error costs on a long-odds contract — see the module
                # docstring's note on the deep tail.
                "relative_error": (
                    abs(over_emp - over_nor) / over_nor if over_nor > 0 else float("nan")
                ),
                "relative_under": (
                    abs(under_emp - under_nor) / under_nor if under_nor > 0 else float("nan")
                ),
            }
        )
    return pd.DataFrame(rows)


def residual_threshold(market: str, point: float, fair: float) -> float:
    """Where this rung sits in residual space, signed — the tables' own axis.

    A residual is ``outcome − expectation``: the actual margin minus the
    projected margin, the actual total minus the projected total. ``point`` is
    the contract's number, home-normalized for spreads by
    :func:`~velocity.store.schema.contract_key`, and ``fair`` the model's own
    line for that market. The contract's **positive** side — home on a spread,
    over on a total — wins exactly when the residual clears what this returns,
    which is what :func:`rung_bias` needs to know which tail a bet is buying.

    Spreads invert because a home line is a negated margin
    (:meth:`~velocity.models.simulate.SimResult.fair_spread`): a fair spread of
    −6 against a −10.5 rung is a margin expectation of 6 against a threshold of
    10.5, so the rung sits +4.5 of residual out, not −4.5. Getting that sign
    backwards would read every favourite's rung as its own mirror, and hand the
    gate the safe tail's error on the dangerous side.
    """
    return (fair - point) if market == "spread" else (point - fair)


def has_ladder_calibration(league: str) -> bool:
    """Whether the rung gate has a measured shape table for this league.

    The gate is asymmetric where it has nothing to read. A spread or total rung
    in an unmeasured league gets ``None`` from :func:`rung_bias` and is refused,
    but a **moneyline** carries no number to be miscalibrated about, so it
    returns 0.0 and sails through. Wiring a new league's exchange board without
    checking this would therefore ship exactly one market — the only one nothing
    is checking — straight to a live stake. Callers use this to keep a league's
    venues on paper until its table is fitted.
    """
    return any(key == str(league).lower() for key, _market in OFFSET_BIAS)


def rung_bias(league: str, market: str, side: str, threshold: float) -> float | None:
    """Signed error in ``P(this side wins)``, or ``None`` if unmeasured.

    **Positive means the sim overstates this bet** — the dangerous sign, since a
    probability the sim inflates is an edge it can invent. Negative means the
    sim understates it, which this bias can only use to *hide* an edge, never to
    manufacture one.

    ``threshold`` is the rung's place in residual space from
    :func:`residual_threshold`. The positive side wins past it, so it buys the
    over tail when the threshold is above the fair line and the *complement* of
    the under tail when below — and the complement's error is the tail's,
    negated. The negative side is the mirror. ``None`` carries the same meaning
    as in :func:`offset_error`: unmeasured, not safe.
    """
    canonical = _MARKET_ALIASES.get(market, market)
    if canonical is None:
        return 0.0  # a moneyline has no number to be miscalibrated about
    table = OFFSET_BIAS.get((str(league).lower(), canonical))
    if table is None:
        return None
    nearest = round(abs(float(threshold)) - 0.5) + 0.5
    pair = table.get(nearest)
    if pair is None:
        return None
    over_bias, under_bias = pair
    positive = (side == "home") if market == "spread" else (side == "over")
    if positive:
        return over_bias if threshold > 0 else -under_bias
    return under_bias if threshold < 0 else -over_bias


def default_relative_tolerance(tolerance: float = DEFAULT_TOLERANCE) -> float:
    """The relative bar implied by an absolute one — not a second free knob.

    A rung's EV per unit staked moves by its probability error divided by its
    price, so ``tolerance`` at even money already accepts an EV distortion of
    ``tolerance / 0.5``. Holding that same budget across the price range is what
    scales the bar down in the tail, and it is why the absolute and relative
    tests agree exactly at 0.5 rather than having to be kept in sync by hand.
    """
    return float(tolerance) / 0.5


def offset_error(league: str, market: str, offset: float) -> float | None:
    """The measured probability error at this offset, or ``None`` if unmeasured.

    ``None`` means the gate has nothing to say — a market it never measured, or
    an offset past the table. Callers treat that as unknown, not as safe.
    """
    canonical = _MARKET_ALIASES.get(market, market)
    if canonical is None:
        return 0.0  # a moneyline has no number to be miscalibrated about
    table = OFFSET_ERROR.get((str(league).lower(), canonical))
    if table is None:
        return None
    # Offsets are half-integers; snap to the nearest measured one.
    nearest = round(abs(float(offset)) - 0.5) + 0.5
    return table.get(nearest)


def offset_is_honest(
    league: str,
    market: str,
    offset: float,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    unmeasured_is_honest: bool = False,
) -> bool:
    """Whether the sim's shape is trustworthy this far from the fair line.

    The **symmetric** answer: it charges both sides the worse tail's error and
    knows nothing about the rung's price. :func:`rung_is_honest` is what the
    slate gates on; this is the conservative view to reach for when the side or
    the price is genuinely unknown, and the one the measurement tables read
    most directly.

    ``unmeasured_is_honest`` decides what an offset beyond the measured table
    means. It defaults to ``False``, matching what :func:`offset_error` says a
    ``None`` is: unknown, not safe. The tables reach 28.5 points, well past
    where a football game realistically lands, and the error there is not
    small enough to extrapolate to zero — so a rung beyond the end is one
    nothing has ever checked, and an EV maximizer will find it precisely
    because nothing has. Pass ``True`` to measure how much the end of the
    table is costing rather than to bet past it.
    """
    error = offset_error(league, market, offset)
    if error is None:
        return unmeasured_is_honest
    return error <= tolerance


def rung_is_honest(
    league: str,
    market: str,
    side: str,
    threshold: float,
    *,
    price: float | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
    relative_tolerance: float | None = None,
    unmeasured_is_honest: bool = False,
) -> bool:
    """Whether the sim's shape can be trusted to price *this side* of this rung.

    The side-aware, price-aware gate. It asks one question — is the amount by
    which the sim overstates **this bet** smaller than what the bet claims? —
    and charges the bar ``min(tolerance, relative_tolerance × price)``: the
    absolute bar because the qualifying test is an edge in probability terms,
    and the relative one because EV per unit staked moves by the error divided
    by the price, so a tolerance fixed in probability is far too loose on a
    6-cent contract. ``price`` is the rung's own de-vigged fair probability;
    without one only the absolute bar applies, since there is nothing to scale
    by. ``relative_tolerance`` defaults to
    :func:`default_relative_tolerance`.

    A bias at or below zero passes: the sim understates this side, so whatever
    edge it reports there is real or better, and blocking it would only cost
    bets this bias cannot have invented. That is a deliberate loosening against
    the symmetric :func:`offset_is_honest`, which charged every rung the worse
    tail's error and so shut off the near-the-line rungs where the bias runs the
    safe way. It survives the measurement's own slack: dilution from the model
    being less sharp than the close shrinks a bias toward zero, and shrinking
    cannot flip a sign.

    ``unmeasured_is_honest`` keeps its meaning from :func:`offset_is_honest` —
    an offset past the table is one nothing has checked, and an EV maximizer
    will find it precisely because nothing has.
    """
    bias = rung_bias(league, market, side, threshold)
    if bias is None:
        return unmeasured_is_honest
    bar = float(tolerance)
    if price is not None and float(price) > 0.0:
        if relative_tolerance is None:
            relative_tolerance = default_relative_tolerance(tolerance)
        bar = min(bar, float(relative_tolerance) * float(price))
    return bias <= bar
