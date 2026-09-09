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

That is the dangerous direction. The sim thinks every rung away from the fair
line is likelier than it is, so it wants to buy them — and an error of three
points swamps the two-point edge the slate bets on. What the plan expected
(trouble concentrated at the key numbers 3 and 7) is not what the data shows:
the bias is broad and one-signed, worst in the shoulders and fading only in
the deep tail where a normal's own mass is small.

So the gate is empirical rather than a key-number rule. :data:`OFFSET_ERROR`
records, per league and market, the worst probability error a normal makes at
each half-point offset from the fair line; :func:`offset_is_honest` answers
whether a rung is inside tolerance. NCAAF spreads pass comfortably (max ~1.9
points); NFL spreads and both totals do not, near the line.

Two caveats worth keeping. First, the gate compares an **absolute** probability
error against a tolerance in the same units as ``min_edge``, because the
qualifying test is an edge in probability terms — a shape error as large as the
edge threshold makes that edge meaningless. But a rung's *EV* moves by the
error divided by its price, so the same 0.6-point miss that is negligible at
even money is a tenth of stake on a 6-cent contract. Both columns come out of
:func:`residual_calibration`; the deep-tail rungs this gate admits are the ones
where that distinction bites, and a future refinement may want the relative
measure there.

Second, residuals here are measured against the market's close,
so they describe outcome shape given a sharp expectation. The sim's own
residual is around *its* projection, which is at best as sharp; if it is less
sharp its residuals are wider and this leptokurtosis is diluted. That makes
the gate conservative, not permissive — the right way to be wrong.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd

# Worst |empirical − normal| probability error at each half-point offset from
# the fair line, by (league, market). Regenerate with
# :func:`residual_calibration` when the datasets grow.
OFFSET_ERROR: Mapping[tuple[str, str], Mapping[float, float]] = {
    # n=4096 completed games, residual sd 12.98 (the sim uses 13.0)
    ("nfl", "spread"): {
        0.5: 0.0300, 1.5: 0.0315, 2.5: 0.0299, 3.5: 0.0322, 4.5: 0.0367,
        5.5: 0.0335, 6.5: 0.0326, 7.5: 0.0313, 8.5: 0.0245, 9.5: 0.0219,
        10.5: 0.0235, 11.5: 0.0245, 12.5: 0.0202, 13.5: 0.0201, 14.5: 0.0161,
        15.5: 0.0117, 16.5: 0.0095, 17.5: 0.0059, 18.5: 0.0065, 19.5: 0.0068,
        20.5: 0.0060,
    },
    # n=4096, residual sd 13.20
    ("nfl", "total"): {
        0.5: 0.0299, 1.5: 0.0297, 2.5: 0.0288, 3.5: 0.0335, 4.5: 0.0317,
        5.5: 0.0303, 6.5: 0.0302, 7.5: 0.0267, 8.5: 0.0206, 9.5: 0.0165,
        10.5: 0.0109, 11.5: 0.0087, 12.5: 0.0093, 13.5: 0.0109, 14.5: 0.0148,
        15.5: 0.0147, 16.5: 0.0164, 17.5: 0.0162, 18.5: 0.0151, 19.5: 0.0147,
        20.5: 0.0134,
    },
    # n=11683, residual sd 15.52 (the sim uses 17.0 — over-dispersed, and a
    # separate finding from this gate's)
    ("ncaaf", "spread"): {
        0.5: 0.0193, 1.5: 0.0191, 2.5: 0.0160, 3.5: 0.0144, 4.5: 0.0159,
        5.5: 0.0142, 6.5: 0.0150, 7.5: 0.0134, 8.5: 0.0113, 9.5: 0.0109,
        10.5: 0.0137, 11.5: 0.0113, 12.5: 0.0088, 13.5: 0.0076, 14.5: 0.0093,
        15.5: 0.0077, 16.5: 0.0056, 17.5: 0.0061, 18.5: 0.0048, 19.5: 0.0027,
        20.5: 0.0026,
    },
    # n=11683, residual sd 16.23
    ("ncaaf", "total"): {
        0.5: 0.0282, 1.5: 0.0258, 2.5: 0.0244, 3.5: 0.0241, 4.5: 0.0250,
        5.5: 0.0269, 6.5: 0.0248, 7.5: 0.0235, 8.5: 0.0236, 9.5: 0.0220,
        10.5: 0.0182, 11.5: 0.0155, 12.5: 0.0110, 13.5: 0.0090, 14.5: 0.0074,
        15.5: 0.0079, 16.5: 0.0080, 17.5: 0.0075, 18.5: 0.0094, 19.5: 0.0102,
        20.5: 0.0109,
    },
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
    empirical and normal tail probabilities on each side and the worst error —
    the table :data:`OFFSET_ERROR` is generated from.
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
                "error": max(abs(over_emp - over_nor), abs(under_emp - under_nor)),
                # The same miss as a fraction of the probability being quoted.
                # A rung's EV moves by (absolute error / price), so this is
                # what an error costs on a long-odds contract — see the module
                # docstring's note on the deep tail.
                "relative_error": (
                    abs(over_emp - over_nor) / over_nor if over_nor > 0 else float("nan")
                ),
            }
        )
        offset += 1.0
    return pd.DataFrame(rows)


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
    unmeasured_is_honest: bool = True,
) -> bool:
    """Whether the sim's shape is trustworthy this far from the fair line.

    ``unmeasured_is_honest`` decides what an offset beyond the measured table
    means. It defaults to ``True`` because the table runs out where a normal's
    own mass does — deep tails, where the measured error was already small and
    shrinking — and refusing everything past it would silently drop the whole
    far ladder.
    """
    error = offset_error(league, market, offset)
    if error is None:
        return unmeasured_is_honest
    return error <= tolerance
