"""MLB rate projections — shrink raw counts to per-PA outcome rates.

The MLB Monte Carlo (Phase M3) samples plate appearances from a small, exhaustive
outcome set, so the model's inputs are **per-PA outcome rates**, not counting
stats. This module turns the counts landed by ``ingest/mlb.py`` into those rates,
with the same "regress a thin sample toward an informed prior" discipline the
NCAAF model uses (:mod:`velocity.features.priors`) — here in *pseudo-PA* rather
than pseudo-games.

Two granularities, chosen to match how the sim combines a matchup:

* **The five shared outcomes** ``k, bb, hbp, hr, in_play`` — mutually exclusive
  and exhaustive over a PA. *Both* batters and pitchers produce this vector (a
  pitcher's ``pa`` is batters faced), so the sim can combine a batter's rates
  with the opposing pitcher's on the same axes (a Log5 / odds-ratio step, later).
  Everything that is not a strikeout, walk, hit-by-pitch or home run is a ball in
  play.
* **The batter-only ball-in-play profile** ``single, double, triple, out_bip`` —
  the conditional hit-type split *given* a ball in play. Season pitching splits
  carry no 1B/2B/3B-allowed breakdown, so this is a batter refinement; the sim
  pairs it with a league/pitcher BABIP downstream.

Shrinkage is a pseudo-count update: ``(count + strength · prior_rate) / (pa +
strength)``. Because each prior vector sums to 1 and the observed counts sum to
``pa``, the shrunk vector **sums to 1 by construction** — the model can never
emit a PA whose outcome probabilities don't add up. Park factors apply
multiplicatively to the HR rate and renormalize, so a neutral park (factor 1) is
the identity.

The single per-outcome ``strength`` is a deliberate simplification: real stats
stabilize at very different rates (K% fast, BABIP slow). It is a config knob to
tune in the backtest phase, not a claim that all rates regress alike.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# The five per-PA outcomes shared by batters and pitchers. Mutually exclusive and
# exhaustive: anything not K/BB/HBP/HR is a ball in play.
PA_OUTCOMES = ["k", "bb", "hbp", "hr", "in_play"]
# The batter-only conditional breakdown of a ball in play. Sums to 1.
BIP_OUTCOMES = ["single", "double", "triple", "out_bip"]

# League-average per-PA outcome rates (~2020s MLB); each vector sums to 1.
DEFAULT_BAT_PRIOR = {"k": 0.225, "bb": 0.085, "hbp": 0.011, "hr": 0.035, "in_play": 0.644}
DEFAULT_PIT_PRIOR = {"k": 0.225, "bb": 0.080, "hbp": 0.011, "hr": 0.035, "in_play": 0.649}
_PA_PRIORS = {"bat": DEFAULT_BAT_PRIOR, "pit": DEFAULT_PIT_PRIOR}
# A single league baseline for the matchup (Log5 / odds-ratio) combination — the
# common denominator when a batter's and a pitcher's rates are merged. Sums to 1.
LEAGUE_PA_RATE = {"k": 0.225, "bb": 0.0825, "hbp": 0.011, "hr": 0.035, "in_play": 0.6465}
# League conditional hit-type split of a ball in play; sums to 1. The hit share
# is league BABIP (~.285): of balls in play, ~28-29% fall for hits.
DEFAULT_BIP_PRIOR = {"single": 0.212, "double": 0.065, "triple": 0.008, "out_bip": 0.715}


@dataclass(frozen=True)
class RateConfig:
    """Regression strengths, in pseudo-PA (see module docstring)."""

    pa_prior_strength: float = 300.0
    bip_prior_strength: float = 200.0

    def __post_init__(self) -> None:
        if self.pa_prior_strength < 0 or self.bip_prior_strength < 0:
            raise ValueError("prior strengths must be non-negative")


def _num(frame: pd.DataFrame, col: str) -> pd.Series:
    """A numeric column with nulls as 0 (a missing count contributes nothing)."""
    return pd.to_numeric(frame[col], errors="coerce").fillna(0.0)


def _components(stats: pd.DataFrame) -> pd.Series:
    """The non-in-play PA outcomes: K + BB + HBP + HR."""
    return _num(stats, "k") + _num(stats, "bb") + _num(stats, "hbp") + _num(stats, "hr")


def _effective_pa(stats: pd.DataFrame) -> pd.Series:
    """PA, but never less than the events already accounted for.

    A messy row can carry a null/understated ``pa`` with real component counts
    (see the M1 tolerance fixture). Using ``max(pa, components)`` as the
    denominator keeps it consistent with the numerator, so the shrunk outcome
    vector still sums to 1 rather than trusting a bad ``pa``.
    """
    pa = _num(stats, "pa")
    components = _components(stats)
    return pa.where(pa >= components, components)


def _in_play_count(stats: pd.DataFrame) -> pd.Series:
    """Balls in play = effective PA − (K + BB + HBP + HR); non-negative by design."""
    return _effective_pa(stats) - _components(stats)


def project_pa_rates(stats: pd.DataFrame, config: RateConfig | None = None) -> pd.DataFrame:
    """Shrink counts into the five shared per-PA outcome rates (both roles).

    ``stats`` is a :class:`~velocity.store.schema.BaseballStats` frame. Each row's
    observed counts are regressed toward its role's league prior with
    ``config.pa_prior_strength`` pseudo-PA. Returns ``player_id, role`` plus the
    :data:`PA_OUTCOMES` columns, which sum to 1 across each row by construction.
    """
    config = config or RateConfig()
    strength = config.pa_prior_strength
    roles = stats["role"].astype(str)
    pa = _effective_pa(stats)

    counts = {
        "k": _num(stats, "k"),
        "bb": _num(stats, "bb"),
        "hbp": _num(stats, "hbp"),
        "hr": _num(stats, "hr"),
        "in_play": _in_play_count(stats),
    }
    out = pd.DataFrame({"player_id": stats["player_id"].astype(str), "role": roles})
    for outcome in PA_OUTCOMES:
        prior_rate = roles.map(lambda r, o=outcome: _PA_PRIORS[r][o]).astype(float)
        out[outcome] = (counts[outcome] + strength * prior_rate) / (pa + strength)
    return out.reset_index(drop=True)


def project_bip_profile(stats: pd.DataFrame, config: RateConfig | None = None) -> pd.DataFrame:
    """Shrink a batter's ball-in-play hit-type split (conditional, sums to 1).

    Only ``role == "bat"`` rows are projected (pitching splits lack the 1B/2B/3B
    breakdown). ``out_bip`` is the residual of balls in play that were outs.
    Returns ``player_id`` plus the :data:`BIP_OUTCOMES` columns.
    """
    config = config or RateConfig()
    strength = config.bip_prior_strength
    bat = stats[stats["role"].astype(str) == "bat"]
    singles = _num(bat, "singles")
    doubles = _num(bat, "doubles")
    triples = _num(bat, "triples")
    hits = singles + doubles + triples
    # As with effective PA, never let the hit counts exceed the balls in play, so
    # the residual out_bip stays non-negative and the profile sums to 1.
    raw_in_play = _in_play_count(bat)
    in_play = raw_in_play.where(raw_in_play >= hits, hits)
    out_bip = in_play - hits
    counts = {"single": singles, "double": doubles, "triple": triples, "out_bip": out_bip}

    out = pd.DataFrame({"player_id": bat["player_id"].astype(str)})
    for outcome in BIP_OUTCOMES:
        prior_rate = DEFAULT_BIP_PRIOR[outcome]
        out[outcome] = (counts[outcome] + strength * prior_rate) / (in_play + strength)
    return out.reset_index(drop=True)


def apply_hr_park_factor(rates: pd.DataFrame, factor: pd.Series | float) -> pd.DataFrame:
    """Scale the HR rate by a park ``factor`` and renormalize the PA outcomes.

    ``factor`` is a multiplicative HR park factor (1.0 = neutral, >1 favors home
    runs); a Series aligns per row. The HR rate is multiplied and the five
    :data:`PA_OUTCOMES` are renormalized to sum to 1, so a neutral factor is the
    identity and the sum-to-one invariant is preserved.
    """
    out = rates.copy()
    out["hr"] = out["hr"] * factor
    total = out[PA_OUTCOMES].sum(axis=1)
    out[PA_OUTCOMES] = out[PA_OUTCOMES].div(total, axis=0)
    return out
