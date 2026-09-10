"""Is the sim honest at a given ladder rung? — the E8 calibration gate.

An exchange lists a whole ladder per game: ~25 spread rungs and ~25 total
rungs, each its own binary contract. The sim can price every one of them
(``model_probability`` accepts any point), but *can* is not *should*: the sim
draws a rounded bivariate normal, and a real football residual is not normal.

Measured on the committed datasets — actual outcome minus the market's own
closing number, which is the sharpest per-game expectation available, so this
isolates the shape of game outcomes rather than the model's aim — the residual
is **leptokurtic**: more mass near zero, thinner shoulders. A normal fitted to
the same standard deviation therefore *overstates* the chance of landing past
any threshold, on both sides at once. For NFL spreads that overstatement is
2.0–3.7 points of probability from half a point out to thirteen.

That is the dangerous direction near the line. The sim thinks a rung in the
shoulders is likelier than it is, so it wants to buy it — and an error of
three points swamps the two-point edge the slate bets on. What the plan
expected (trouble concentrated at the key numbers 3 and 7) is not what the
data shows: the bias is broad, worst in the shoulders.

It does **not** stay one-signed, and it does not fade away. Past roughly
fifteen points the sign flips — the real tail is *fatter* than the fitted
normal, not thinner — and the absolute error then plateaus rather than
shrinking: NFL totals sit at 1.0-1.3 points from 20.5 out to 28.5, and NCAAF
totals climb from 0.8 at 15.5 to 1.5 at 25.5. An earlier version of this
module stopped measuring at 20.5 and let anything past it through on the
argument that the error out there "was already small and shrinking". The
measurement above says otherwise, and the first live exchange board bet
straight into that unexamined region — every qualifying rung on it was 15 to
22 points out, because the gate had blocked everything nearer the line. So
the tables now run to 28.5 and a rung past their end is refused rather than
assumed innocent.

So the gate is empirical rather than a key-number rule. :data:`OFFSET_ERROR`
records, per league and market, the worst probability error a normal makes at
each half-point offset from the fair line; :func:`offset_is_honest` answers
whether a rung is inside tolerance. NCAAF spreads pass comfortably (max ~1.9
points); NFL spreads and both totals do not, near the line.

**The two tails do not move together, and the gate now reads them apart.**
:data:`OFFSET_BIAS` keeps the error *signed* per tail — positive where the
normal overstates that tail — because only one sign is dangerous. A rung whose
own side the sim **overstates** is one the sim wants to buy for a reason that
is not there; a rung whose side it **understates** cannot have its edge
invented by this bias, only hidden. Splitting them says which is which, and
the split is large: on NFL spreads the over tail flips negative past 16.5
while the under tail stays positive out to 24.5, and on both leagues' totals
the *under* tail is the overstated one from 9.5 out (NFL +0.013 to +0.016 at
16.5–21.5) while the over tail has already gone negative. The old symmetric
``max(|over|, |under|)`` charged every rung the worse tail's error, which both
blocked near-the-line rungs the bias could only help and said nothing about
which deep rung was the trap.

**And the absolute bar is in the wrong units for the deep tail.** It compares a
probability error against a tolerance in ``min_edge``'s units, which is right
for the qualifying test — a shape error as large as the edge threshold makes
that edge meaningless. But a rung's *EV per unit staked* moves by the error
divided by its price, so the same 0.6-point miss that is negligible at even
money is a tenth of stake on a 6-cent contract. Measured on the committed
datasets that ratio does not shrink with distance, it **grows**: NFL totals run
from 0.06 of stake at half a point to 0.46 at 28.5, NFL spreads 0.06 to 0.34,
NCAAF totals 0.06 to 0.31. So the absolute bar admitted precisely the rungs
where a shape error costs the most, which is why the first live exchange board
came back all deep tail. :func:`rung_is_honest` therefore charges the bar
``min(tolerance, relative_tolerance × price)`` — the same EV budget the
absolute bar already accepts at even money, held constant across the price
range, which is why the two tests agree exactly at 0.5 instead of being two
independent knobs. What survives it in the deep tail is the *safe* half of each
ladder — the side the sim understates — plus NCAAF spreads, whose bias is small
and symmetric enough to pass on both.

Residuals here are measured against the market's close,
so they describe outcome shape given a sharp expectation. The sim's own
residual is around *its* projection, which is at best as sharp; if it is less
sharp its residuals are wider and this leptokurtosis is diluted. That makes
the gate conservative, not permissive — the right way to be wrong.

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

import pandas as pd

# Signed probability error at each half-point offset from the fair line, as
# ``(over_bias, under_bias)`` per (league, market): the normal's tail
# probability minus the empirical one, so **positive means the sim overstates
# that tail**. Generated by :func:`residual_calibration` from the committed
# datasets; regenerate when they grow.
OFFSET_BIAS: Mapping[tuple[str, str], Mapping[float, tuple[float, float]]] = {
    # n=4097 completed games, residual sd 12.98 (the sim uses 13.0)
    ("nfl", "spread"): {
        0.5: (+0.0301, +0.0088), 1.5: (+0.0316, +0.0116), 2.5: (+0.0300, +0.0128),
        3.5: (+0.0323, +0.0139), 4.5: (+0.0368, +0.0162), 5.5: (+0.0335, +0.0151),
        6.5: (+0.0327, +0.0216), 7.5: (+0.0313, +0.0227), 8.5: (+0.0245, +0.0204),
        9.5: (+0.0219, +0.0189), 10.5: (+0.0236, +0.0218), 11.5: (+0.0239, +0.0245),
        12.5: (+0.0156, +0.0203), 13.5: (+0.0127, +0.0201), 14.5: (+0.0097, +0.0161),
        15.5: (+0.0028, +0.0117), 16.5: (+0.0002, +0.0095), 17.5: (-0.0018, +0.0059),
        18.5: (-0.0040, +0.0065), 19.5: (-0.0068, +0.0054), 20.5: (-0.0059, +0.0059),
        21.5: (-0.0068, +0.0063), 22.5: (-0.0081, +0.0033), 23.5: (-0.0075, +0.0014),
        24.5: (-0.0064, +0.0006), 25.5: (-0.0049, -0.0010), 26.5: (-0.0055, -0.0026),
        27.5: (-0.0052, -0.0022), 28.5: (-0.0046, -0.0024),
    },
    # n=4097 completed games, residual sd 13.20
    ("nfl", "total"): {
        0.5: (+0.0298, -0.0173), 1.5: (+0.0297, -0.0171), 2.5: (+0.0287, -0.0131),
        3.5: (+0.0335, -0.0094), 4.5: (+0.0317, -0.0045), 5.5: (+0.0303, -0.0042),
        6.5: (+0.0302, -0.0056), 7.5: (+0.0267, -0.0026), 8.5: (+0.0205, -0.0021),
        9.5: (+0.0165, +0.0043), 10.5: (+0.0108, +0.0047), 11.5: (+0.0086, +0.0047),
        12.5: (+0.0093, +0.0092), 13.5: (+0.0083, +0.0108), 14.5: (+0.0064, +0.0147),
        15.5: (+0.0026, +0.0146), 16.5: (+0.0007, +0.0163), 17.5: (-0.0026, +0.0161),
        18.5: (-0.0039, +0.0150), 19.5: (-0.0061, +0.0146), 20.5: (-0.0052, +0.0133),
        21.5: (-0.0061, +0.0130), 22.5: (-0.0073, +0.0110), 23.5: (-0.0080, +0.0106),
        24.5: (-0.0102, +0.0098), 25.5: (-0.0095, +0.0100), 26.5: (-0.0092, +0.0089),
        27.5: (-0.0084, +0.0078), 28.5: (-0.0052, +0.0066),
    },
    # n=11972 completed games, residual sd 15.54 (the sim uses 18.2 — deliberately wider than the
    # residual around the market close, which is a sharper expectation than
    # the model's own)
    ("ncaaf", "spread"): {
        0.5: (+0.0191, +0.0055), 1.5: (+0.0188, +0.0104), 2.5: (+0.0155, +0.0101),
        3.5: (+0.0143, +0.0126), 4.5: (+0.0150, +0.0160), 5.5: (+0.0137, +0.0135),
        6.5: (+0.0119, +0.0148), 7.5: (+0.0129, +0.0131), 8.5: (+0.0098, +0.0109),
        9.5: (+0.0086, +0.0103), 10.5: (+0.0098, +0.0132), 11.5: (+0.0098, +0.0111),
        12.5: (+0.0080, +0.0087), 13.5: (+0.0064, +0.0074), 14.5: (+0.0094, +0.0086),
        15.5: (+0.0054, +0.0077), 16.5: (+0.0057, +0.0062), 17.5: (+0.0063, +0.0054),
        18.5: (+0.0050, +0.0052), 19.5: (+0.0024, +0.0031), 20.5: (+0.0028, +0.0028),
        21.5: (+0.0021, +0.0033), 22.5: (+0.0017, +0.0027), 23.5: (+0.0011, +0.0026),
        24.5: (+0.0009, +0.0034), 25.5: (-0.0001, +0.0032), 26.5: (-0.0007, +0.0027),
        27.5: (+0.0005, +0.0014), 28.5: (-0.0003, +0.0012),
    },
    # n=11684 completed games, residual sd 16.24
    ("ncaaf", "total"): {
        0.5: (+0.0282, -0.0137), 1.5: (+0.0258, -0.0146), 2.5: (+0.0243, -0.0133),
        3.5: (+0.0241, -0.0086), 4.5: (+0.0249, -0.0077), 5.5: (+0.0269, -0.0073),
        6.5: (+0.0247, -0.0045), 7.5: (+0.0235, -0.0012), 8.5: (+0.0236, -0.0020),
        9.5: (+0.0220, -0.0019), 10.5: (+0.0182, -0.0014), 11.5: (+0.0155, +0.0010),
        12.5: (+0.0110, +0.0014), 13.5: (+0.0090, +0.0036), 14.5: (+0.0074, +0.0042),
        15.5: (+0.0079, +0.0059), 16.5: (+0.0080, +0.0070), 17.5: (+0.0046, +0.0075),
        18.5: (+0.0027, +0.0094), 19.5: (+0.0012, +0.0102), 20.5: (+0.0009, +0.0109),
        21.5: (+0.0008, +0.0119), 22.5: (-0.0009, +0.0119), 23.5: (-0.0007, +0.0132),
        24.5: (-0.0024, +0.0142), 25.5: (-0.0042, +0.0146), 26.5: (-0.0046, +0.0140),
        27.5: (-0.0045, +0.0126), 28.5: (-0.0042, +0.0116),
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


def residual_calibration(
    games: pd.DataFrame,
    market: str,
    *,
    max_offset: float = 20.5,
) -> pd.DataFrame:
    """Measure how far a normal misses at each half-point offset from the line.

    ``games`` needs final scores plus the market's closing number
    (``spread_line`` / ``total_line``). Returns one row per offset with the
    empirical and normal tail probabilities on each side, the **signed** error
    per tail (``over_bias`` / ``under_bias``, positive where the normal
    overstates that tail), the worst of the two as ``error``, and each tail's
    error as a fraction of the probability quoted there. :data:`OFFSET_BIAS` is
    generated from the signed columns; the relative ones are what
    :func:`rung_is_honest` scales its bar by, since a rung's EV moves by its
    error divided by its price.
    """
    column = "spread_line" if market == "spread" else "total_line"
    frame = games.dropna(subset=["home_score", "away_score", column])
    if frame.empty:
        return pd.DataFrame(columns=["offset", "empirical_over", "normal_over", "error"])

    if market == "spread":
        outcome = frame["home_score"] - frame["away_score"]
    else:
        outcome = frame["home_score"] + frame["away_score"]
    residual = (outcome - frame[column]).astype(float).to_numpy()
    mu, sd = float(residual.mean()), float(residual.std(ddof=1))

    rows = []
    offset = 0.5
    while offset <= max_offset:
        over_emp = float((residual > offset).mean())
        over_nor = _normal_sf(offset, mu, sd)
        under_emp = float((residual < -offset).mean())
        under_nor = 1.0 - _normal_sf(-offset, mu, sd)
        rows.append(
            {
                "offset": offset,
                "empirical_over": over_emp,
                "normal_over": over_nor,
                "empirical_under": under_emp,
                "normal_under": under_nor,
                # Signed, and in the dangerous direction when positive: the
                # normal claims more mass past this threshold than the games
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
        offset += 1.0
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
