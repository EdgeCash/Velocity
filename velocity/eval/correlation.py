"""How correlated are two bets on the same game? — measured, not assumed.

:func:`velocity.wagering.portfolio.correlation_scale` de-scales exposure inside
a correlation group by ``1 / (1 + (n-1)ρ)``, and the slate runner groups by game
with a single ``group_correlation = 0.5`` for every pair. That constant was set
when one bet per view was the rule. Exchanges changed the shape of a card: a
ladder puts several contracts on one game's total, so what ρ actually *is* for a
given pair now decides real money.

This module measures it. The quantity is the correlation between the two bets'
**win indicators** — that is what inflates the variance of combined stake, which
is what the de-scaling exists to answer. Two sources:

* :func:`historical_correlation` reads the committed games datasets and computes
  ρ for a pair class from settled finals against closing numbers. That is a
  large sample (NFL 2011-, NCAAF 2015-) and it is about *outcomes*.
* :func:`realized_correlation` reads settled ledger rows and computes ρ from
  bets we actually held, grouped by game. That is the live counterpart, and it
  is the only one that sees our own book of business — which pair classes we
  really end up holding together, and at what venues.

**Nothing here changes staking.** The measurement is deliberately separate from
the sizing path: ``portfolio.py`` still applies the flat 0.5. Turning a measured
ρ into a staking input is a decision with real exposure attached, and it wants
the realized table alongside the historical one before it is made.

What the historical measurement says (see :data:`HISTORICAL_NOTES`):

The headline is that the flat 0.5 is wrong in both directions, and the largest
error is on the pair a card carries most often. Spread and total on the same
game are close to independent — NFL ρ −0.006 (95% CI [−0.036, +0.026]) on 3,952
non-push games, flat across all fifteen seasons. De-scaling that pair as if it
were half-correlated under-bets both legs by about a third.

The exception is blowout pricing, and only in college. NCAAF favourite-covers ×
over is ρ +0.067 overall, but that average hides a clean monotone structure:
flat (+0.008, CI [−0.015, +0.032]) for every favourite under 14, then +0.102 at
14-21, +0.185 at 21-28, +0.260 past 28. Reading it home-relative instead gives
+0.050 and hides the structure, which is why the module restates every spread
from the favourite's side. Physically plain — a four-touchdown
college favourite covering *is* a game with points in it. It is stable season by
season within the big-favourite slice (sd 0.065 across eleven seasons), so the
apparent recent "trend" in the pooled number is a mix effect: the dataset simply
carries more mismatches lately. NFL almost never prices a favourite that big
(2-5% of games) and shows nothing at any size.

The ladder half is the opposite story, and far better determined. Two rungs on
the same side of one total are highly correlated when close together and much
less so apart: NFL +0.88 at two points, +0.59 at eight, +0.22 at twenty; NCAAF
+0.91 / +0.66 / +0.32. Per-season sd is 0.016-0.039, so these need no further
confirmation. The practical consequence is that our *own* rungs are not the
correlated ones — a rung priced at +900 is priced there because it sits sixteen
to twenty points off the book number, which is exactly where ρ has fallen to
0.2-0.4. The flat 0.5 over-de-scales them too.

Opposite sides are negatively correlated and the formula has no way to say so.
An exchange "under 25.5" against a book "over 44.5" cannot both lose: ρ −0.588
NFL, −0.655 NCAAF. A true middle (over X, under X+8) both-wins 19-23% of the
time at ρ −0.611 / −0.666. Applying a positive ρ to a hedge charges it for adding
risk it removes.

One caveat the outcome data cannot speak to, and the reason a measured ρ should
not be used naked: these legs share a *model*, not only a game. If the sim is
wrong about a game's pace, the over and the team total are both wrong even
though their outcomes are near-independent under the true distribution. Outcome
correlation is not correlation of our edge estimates, and the second is what
actually loses money together. Any staking use of this table wants a floor
under it for that.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias

import numpy as np
import pandas as pd

#: A vector of win indicators. Callers hold pandas Series as often as arrays,
#: and both are accepted rather than converted at every call site.
BoolVector: TypeAlias = "Sequence[bool] | np.ndarray | pd.Series"

__all__ = [
    "PairMeasurement",
    "HISTORICAL_NOTES",
    "SAME_SIDE_SEPARATIONS",
    "correlation",
    "correlation_ci",
    "pair_class",
    "historical_correlation",
    "realized_correlation",
    "ladder_separation_curve",
]


# Separations (in points) at which the same-side rung curve is reported. Two
# points is the tightest a real ladder offers; twenty is past where any rung we
# buy sits, and the curve is flat enough by then to extrapolate.
SAME_SIDE_SEPARATIONS: tuple[float, ...] = (2.0, 4.0, 8.0, 12.0, 16.0, 20.0)

# The favourite sizes at which NCAAF spread×total stops being independent.
# Edges of the buckets the measurement found a monotone rise across.
FAVOURITE_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 3.0), (3.0, 7.0), (7.0, 10.0), (10.0, 14.0),
    (14.0, 21.0), (21.0, 28.0), (28.0, float("inf")),
)

# Recorded so a reader of the sizing code can find the numbers without rerunning
# the measurement. ``(league, pair class) → ρ``, keyed by the same labels
# :func:`pair_class` produces, so a realized row and a banked one line up.
#
# This is a *cache of the committed datasets*, and caches go stale —
# ``tests/test_correlation.py`` re-measures every entry and fails on drift, the
# same guard ``OFFSET_BIAS`` carries. ``scripts/measure_correlation.py`` prints
# the values, with intervals and sample sizes.
HISTORICAL_NOTES: Mapping[tuple[str, str], float] = {
    # NFL — spread and total are independent, at every favourite size
    ("nfl", "spread x total"): -0.006,
    ("nfl", "spread x total, favourite 3-7"): -0.014,
    ("nfl", "moneyline x spread"): +0.710,
    ("nfl", "total same-side 2p"): +0.878,
    ("nfl", "total same-side 4p"): +0.766,
    ("nfl", "total same-side 8p"): +0.588,
    ("nfl", "total same-side 12p"): +0.443,
    ("nfl", "total same-side 16p"): +0.315,
    ("nfl", "total same-side 20p"): +0.220,
    ("nfl", "total hedge 8p"): -0.588,
    ("nfl", "total middle 8p"): -0.611,
    # NCAAF — independent under 14 points of favourite, monotone above it
    ("ncaaf", "spread x total"): +0.067,
    ("ncaaf", "spread x total, favourite 3-7"): +0.001,
    ("ncaaf", "spread x total, favourite 14-21"): +0.102,
    ("ncaaf", "spread x total, favourite 21-28"): +0.185,
    # Refreshed 2026-09-12: main's current-season dataset top-up moved this
    # one entry from +0.260, which is exactly the drift this cache's test
    # exists to catch. Every other NCAAF and NFL entry re-measured unchanged.
    ("ncaaf", "spread x total, favourite 28+"): +0.262,
    ("ncaaf", "moneyline x spread"): +0.568,
    ("ncaaf", "total same-side 2p"): +0.912,
    ("ncaaf", "total same-side 4p"): +0.815,
    ("ncaaf", "total same-side 8p"): +0.655,
    ("ncaaf", "total same-side 12p"): +0.530,
    ("ncaaf", "total same-side 16p"): +0.417,
    ("ncaaf", "total same-side 20p"): +0.320,
    ("ncaaf", "total hedge 8p"): -0.655,
    ("ncaaf", "total middle 8p"): -0.666,
}


@dataclass(frozen=True)
class PairMeasurement:
    """One pair class: its correlation, a bootstrap interval, and the sample."""

    pair: str
    rho: float
    lo: float
    hi: float
    n: int
    #: What ``correlation_scale`` would apply at this ρ for a two-bet group,
    #: next to what the flat 0.5 applies. Reported rather than used.
    scale: float = 0.0
    flat_scale: float = 0.0

    @property
    def includes_zero(self) -> bool:
        """Whether the interval admits independence."""
        return bool(self.lo <= 0.0 <= self.hi)

    @property
    def stake_error(self) -> float:
        """Signed fraction by which the flat 0.5 mis-stakes this pair.

        Positive means the flat assumption *over*-bets relative to the measured
        ρ; negative means it under-bets.
        """
        if not np.isfinite(self.scale) or self.scale == 0.0:
            return float("nan")
        return self.flat_scale / self.scale - 1.0


def correlation(a: BoolVector, b: BoolVector, *, min_n: int = 30) -> float:
    """Pearson ρ between two win-indicator vectors, NaN when undetermined.

    Binary indicators, so this is the phi coefficient; it is the quantity the
    variance of a combined stake depends on, which is what the caller wants.
    A constant vector (every bet on that side won, or none did) has no
    correlation to report rather than a zero one.
    """
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if x.size != y.size:
        raise ValueError("correlation needs two equal-length vectors")
    if x.size < min_n or x.std() == 0.0 or y.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def correlation_ci(a: BoolVector, b: BoolVector, *, reps: int = 2000,
                   seed: int = 20260911,
                   min_n: int = 30) -> tuple[float, float, float]:
    """``(ρ, lo, hi)`` with a 95% bootstrap interval resampled over games.

    Bootstrapped rather than taken from normal theory: these are binary
    indicators at sometimes-lopsided base rates, where the Fisher transform is
    not to be trusted, and a resample over games is the honest unit anyway.
    """
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    point = correlation(x, y, min_n=min_n)
    if not np.isfinite(point):
        return point, float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    n = x.size
    draws = np.empty(reps, dtype=float)
    for i in range(reps):
        idx = rng.integers(0, n, n)
        draws[i] = correlation(x[idx], y[idx], min_n=min_n)
    return point, float(np.nanpercentile(draws, 2.5)), float(np.nanpercentile(draws, 97.5))


#: Which way a side wins. ``over`` and ``home`` win as the quantity rises;
#: ``under`` and ``away`` win as it falls. Needed to tell a hedge from a middle,
#: which is not something the sides alone can say.
_UP_SIDES = frozenset({"over", "home", "yes"})
_DOWN_SIDES = frozenset({"under", "away", "no"})


def _direction(side: str) -> int:
    """``+1`` if the side wins as the quantity rises, ``-1`` if it falls, else 0."""
    s = str(side).strip().lower()
    if s in _UP_SIDES:
        return 1
    if s in _DOWN_SIDES:
        return -1
    return 0


def _as_point(value: Any) -> float | None:
    """A number, or ``None`` for anything that cannot serve as one.

    Points arrive from dataframes, so missing shows up as ``None``, ``NaN``, an
    empty string, or a non-numeric object. Normalising here means every branch
    below tests ``is None`` once instead of repeating the three-way check.
    """
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(out) else out


def pair_class(market_a: Any, side_a: Any, point_a: Any,
               market_b: Any, side_b: Any, point_b: Any) -> str:
    """A canonical label for the correlation class two bets fall into.

    The label is what the measurement is keyed on, so it has to capture exactly
    what ρ depends on and nothing else. Across markets that is the pair of
    markets. On one market it is the gap between the numbers *and* how the two
    winning regions sit — which is three cases, not two:

    * **same-side** — both win together or neither does, positively correlated,
      more so the closer the numbers.
    * **hedge** — opposite sides, and the region between them belongs to
      neither: an ``over 44.5`` against an ``under 36.5`` cannot both win.
    * **middle** — opposite sides, and the region between them belongs to both:
      an ``over 36.5`` against an ``under 44.5`` both win on any total from 37
      to 44.

    Hedge and middle are both negatively correlated and would fall in one bucket
    if the label went by side alone, but they are different bets — one removes
    all joint risk, the other pays twice — so they are kept apart.

    Order-independent, so a card's pairs land in one bucket however they were
    enumerated.
    """
    ma, mb = str(market_a), str(market_b)
    if ma != mb:
        first, second = sorted((ma, mb))
        return f"{first} x {second}"

    pa, pb = _as_point(point_a), _as_point(point_b)
    same_side = str(side_a) == str(side_b)
    where = "same-side" if same_side else _opposite_shape(side_a, pa, side_b, pb)
    if pa is None or pb is None:
        return f"{ma} {where}"
    return f"{ma} {where} {abs(pa - pb):g}p"


def _opposite_shape(side_a: Any, point_a: float | None,
                    side_b: Any, point_b: float | None) -> str:
    """``middle`` when the two winning regions overlap, else ``hedge``.

    The up-side wins above its number and the down-side below its own, so the
    regions overlap exactly when the up-side's number is the lower of the two.
    Without a readable direction or a number on both legs there is nothing to
    decide on, and the pair stays the unqualified ``opposite-side``.
    """
    da, db = _direction(side_a), _direction(side_b)
    if da == 0 or db == 0 or da == db:
        return "opposite-side"
    if point_a is None or point_b is None:
        return "opposite-side"
    up, down = (point_a, point_b) if da > 0 else (point_b, point_a)
    return "middle" if up < down else "hedge"


def _finals(games: pd.DataFrame) -> pd.DataFrame:
    """Graded games with closing numbers, pushes kept but flagged.

    ``fav_margin``/``fav_line`` restate the spread from the favourite's side so a
    measurement is not averaged across home favourites and home dogs — which is
    how a real effect hides inside a near-zero pooled number.
    """
    need = ["home_score", "away_score", "spread_line", "total_line"]
    g = games.dropna(subset=need)
    margin = (g["home_score"] - g["away_score"]).astype(float)
    total = (g["home_score"] + g["away_score"]).astype(float)
    spread = g["spread_line"].astype(float)
    out = pd.DataFrame({
        "margin": margin.to_numpy(), "total": total.to_numpy(),
        "spread_line": spread.to_numpy(), "total_line": g["total_line"].astype(float).to_numpy(),
        # positive spread_line means the home side is favoured
        "fav_margin": np.where(spread >= 0, margin, -margin),
        "fav_line": spread.abs().to_numpy(),
    })
    if "season" in g.columns:
        out["season"] = g["season"].astype(int).to_numpy()
    return out


def _no_push(d: pd.DataFrame) -> pd.DataFrame:
    """Games where neither leg pushed — a push is a refund, not a loss."""
    return d[(d["total"] != d["total_line"]) & (d["margin"] != d["spread_line"])]


def historical_correlation(games: pd.DataFrame, *, reps: int = 2000,
                           separations: Iterable[float] = SAME_SIDE_SEPARATIONS,
                           flat_rho: float = 0.5) -> list[PairMeasurement]:
    """Measure every pair class from settled finals against closing numbers.

    The closing number is the sharpest per-game expectation available, so a bet
    defined against it isolates the *shape* of joint outcomes rather than the
    model's aim — the same reasoning :mod:`velocity.eval.ladders` uses.
    """
    d = _no_push(_finals(games))
    if d.empty:
        return []
    flat = _two_bet_scale(flat_rho)
    over = d["total"] > d["total_line"]
    under = d["total"] < d["total_line"]
    fav_covers = d["fav_margin"] > d["fav_line"]
    out: list[PairMeasurement] = []

    def add(pair: str, a: BoolVector, b: BoolVector) -> None:
        rho, lo, hi = correlation_ci(a, b, reps=reps)
        out.append(PairMeasurement(pair=pair, rho=rho, lo=lo, hi=hi, n=int(len(d)),
                                   scale=_two_bet_scale(rho), flat_scale=flat))

    # Every label comes from pair_class rather than a literal, so a historical
    # row and a realized row for the same shape carry the same name and can be
    # read side by side. Hardcoding them drifted once already: "spread x
    # moneyline" against the sorted "moneyline x spread" the function emits.
    # The points below are representative — only the gap between them matters.
    ref, spread_ref = 44.5, -3.5
    add(pair_class("spread", "home", spread_ref, "total", "over", ref), fav_covers, over)
    add(pair_class("spread", "home", spread_ref, "moneyline", "home", None),
        fav_covers, d["fav_margin"] > 0)
    for gap in separations:
        add(pair_class("total", "under", ref, "total", "under", ref - gap),
            under, d["total"] < d["total_line"] - gap)
        # Hedge: over the line against under a *lower* number — the points
        # between belong to neither, so they cannot both win.
        add(pair_class("total", "over", ref, "total", "under", ref - gap),
            over, d["total"] < d["total_line"] - gap)
        # Middle: over a lower number against under the line — the points
        # between belong to both.
        add(pair_class("total", "over", ref - gap, "total", "under", ref),
            d["total"] > d["total_line"] - gap, under)
        add(pair_class("spread", "home", spread_ref, "spread", "home", spread_ref - gap),
            fav_covers, d["fav_margin"] > d["fav_line"] + gap)
    # The conditioning that matters: spread×total is independent except where a
    # blowout is priced, so report it per favourite bucket rather than pooled.
    for lo_f, hi_f in FAVOURITE_BUCKETS:
        part = d[(d["fav_line"] >= lo_f) & (d["fav_line"] < hi_f)]
        if len(part) < 120:
            continue
        rho, lo, hi = correlation_ci(part["fav_margin"] > part["fav_line"],
                                     part["total"] > part["total_line"], reps=reps)
        tail = "+" if not np.isfinite(hi_f) else f"-{hi_f:g}"
        label = f"spread x total, favourite {lo_f:g}{tail}"
        out.append(PairMeasurement(pair=label, rho=rho, lo=lo, hi=hi, n=int(len(part)),
                                   scale=_two_bet_scale(rho), flat_scale=flat))
    return out


def ladder_separation_curve(games: pd.DataFrame, *,
                            separations: Iterable[float] = SAME_SIDE_SEPARATIONS,
                            ) -> pd.DataFrame:
    """Per-season ρ for same-side total rungs, to show the curve is stable.

    Returns one row per separation with the pooled ρ and the spread of the
    per-season estimates. A pooled number that moves season to season would mean
    the curve cannot be used; these do not.
    """
    d = _no_push(_finals(games))
    if d.empty or "season" not in d.columns:
        return pd.DataFrame(columns=["separation", "rho", "seasons",
                                    "rho_min", "rho_max", "sd"])
    rows = []
    for gap in separations:
        per = []
        for _, grp in d.groupby("season"):
            r = correlation(grp["total"] < grp["total_line"],
                            grp["total"] < grp["total_line"] - gap, min_n=60)
            if np.isfinite(r):
                per.append(r)
        pooled = correlation(d["total"] < d["total_line"],
                             d["total"] < d["total_line"] - gap)
        arr = np.asarray(per, dtype=float)
        rows.append({"separation": float(gap), "rho": pooled, "seasons": len(arr),
                     "rho_min": float(arr.min()) if arr.size else float("nan"),
                     "rho_max": float(arr.max()) if arr.size else float("nan"),
                     "sd": float(arr.std()) if arr.size else float("nan")})
    return pd.DataFrame(rows)


#: How each of the ledger's settled results reads as a joint outcome. A push or
#: a tie returns the stake, so it is no evidence about whether two bets moved
#: together and is dropped rather than counted as a loss.
#:
#: Partitioned against the ledger's own vocabulary and checked by
#: ``tests/test_correlation.py``, so a settled result added there has to be
#: classified here rather than silently falling out of the measurement.
_WIN = frozenset({"win"})
_LOSS = frozenset({"loss"})
_REFUNDED = frozenset({"push", "tie"})
_DECIDED = _WIN | _LOSS


def realized_correlation(settled: pd.DataFrame, *, flat_rho: float = 0.5,
                         min_pairs: int = 20, reps: int = 2000,
                         ) -> list[PairMeasurement]:
    """Measure ρ per pair class from settled ledger rows we actually held.

    Takes the ledger's settled records (``game_id``, ``market``, ``side``,
    ``point``, ``result``) and forms every within-game pair, so the classes that
    come out are the ones our cards really carry — which is the half of the
    question the historical datasets cannot answer. Pairs are counted once per
    ordered-by-label class; a class thinner than ``min_pairs`` is omitted rather
    than reported at an interval wide enough to mean nothing.

    Returns an empty list until football settles: exchange rungs placed on a
    Thursday card do not grade until the weekend, and before that the honest
    answer is that there is no live evidence yet.
    """
    need = {"game_id", "market", "side", "result"}
    if settled is None or settled.empty or not need <= set(settled.columns):
        return []
    # Reset the index: an appended ledger can carry duplicate labels, and the
    # point lookup below is by label.
    d = settled.copy().reset_index(drop=True)
    result = d["result"].astype(str).str.lower()
    d["won"] = result.isin(_WIN)
    d = d[result.isin(_DECIDED)]
    if d.empty:
        return []
    point = d["point"] if "point" in d.columns else pd.Series(np.nan, index=d.index)

    buckets: dict[str, list[tuple[bool, bool]]] = {}
    for _, game in d.groupby("game_id"):
        rows = list(game.itertuples())
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                label = pair_class(a.market, a.side, point.get(a.Index),
                                   b.market, b.side, point.get(b.Index))
                buckets.setdefault(label, []).append((bool(a.won), bool(b.won)))

    flat = _two_bet_scale(flat_rho)
    out: list[PairMeasurement] = []
    for label, pairs in sorted(buckets.items()):
        if len(pairs) < min_pairs:
            continue
        left = np.array([p[0] for p in pairs], dtype=float)
        right = np.array([p[1] for p in pairs], dtype=float)
        rho, lo, hi = correlation_ci(left, right, reps=reps, min_n=min_pairs)
        out.append(PairMeasurement(pair=label, rho=rho, lo=lo, hi=hi, n=len(pairs),
                                   scale=_two_bet_scale(rho), flat_scale=flat))
    return out


def _two_bet_scale(rho: float) -> float:
    """``correlation_scale(2, ρ)``, defined for negative ρ so a hedge is visible.

    Mirrors :func:`velocity.wagering.portfolio.correlation_scale` at ``n=2``.
    That function takes ρ in [0, 1] because it is a staking input; here a
    negative ρ is the finding, so it is carried through rather than clamped.
    """
    if not np.isfinite(rho):
        return float("nan")
    denom = 1.0 + rho
    return float("inf") if denom <= 0 else 1.0 / denom
