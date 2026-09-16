"""A DraftKings kicker projection — the Showdown board's missing position.

The sibling of :mod:`velocity.dfs.dst`, and it exists for the same reason one
position over. DK's Showdown roster has a kicker slot and a kicker is routinely
a live captain, but nothing priced one: ``DK_POINTS_PER_STAT`` carries no
``fg`` / ``fga`` / ``xpt`` weight and the correlated prop sim has no kicker
markets, so a kicker collapsed to **0.00 expected points** and the optimizer
took whichever was cheapest. That is the DST bug — the pool joining a position
at zero and the lineup total quietly ignoring it — and the FantasyPros stat-key
census is what surfaced it: those three keys were the only ones in the feed
nothing read.

TWO THINGS HAVE TO BE RIGHT, AND NEITHER IS THE PROJECTION ITSELF

**DK pays by distance; FantasyPros projects a total.** The feed serves ``fg``
(made) with no distance split, while DK pays 3 / 4 / 5 for 0-39, 40-49 and 50+.
The banked kicks supply the mix — 56.0% / 26.9% / 17.2% over 2020-2025, which
is **3.612 points per made field goal**. Scoring every make at 3.0 would
under-price a kicker by about 17%, and under-price the long-range ones most,
which are exactly the captain plays.

**Kicking counts are UNDERdispersed, so a Poisson is the wrong shape.** Within
player-season the banked attempts run variance/mean 0.80 (field goals) and 0.74
(extra points) — a kicker's workload is bounded and regular in a way a
receiver's targets are not. A Poisson would say 1.0 and price both tails far
too wide. Attempts are drawn as a **binomial** whose trial probability is fixed
at ``1 - var/mean`` so the dispersion matches by construction, and makes are a
binomial on those attempts at the kicker's own rate — so a make can never
exceed an attempt, and the two move together. Checked: the structure returns
made variance/mean 0.832 (FG) and 0.755 (PAT) against banked 0.827 and 0.768,
and the expectation reproduces the banked 8.366 DK points per active kicker
game exactly.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from velocity.dfs.scoring import _ID_COLUMNS

KICKER_POSITIONS = frozenset({"K", "PK", "KICKER"})

# DK field-goal points by distance band, and the banked share of made kicks in
# each (2020-2025 regular season, 5,414 makes). The mix is what converts a
# FantasyPros ``fg`` total into DK points.
FG_BAND_POINTS: tuple[float, ...] = (3.0, 4.0, 5.0)
FG_BAND_SHARE: tuple[float, ...] = (0.5597, 0.2687, 0.1716)
POINTS_PER_MADE_FG = float(sum(p * s for p, s in zip(FG_BAND_POINTS, FG_BAND_SHARE,
                                                     strict=True)))
PAT_POINTS = 1.0

# The binomial trial probability that reproduces the banked ATTEMPT dispersion:
# for Binomial(n, p), variance/mean is exactly ``1 - p``, so p is read straight
# off the measurement (0.80 and 0.74 within player-season, 192 kicker-seasons).
# ``n`` follows from the projection, and is notional — "chances at a kick",
# not a literal count of drives.
FG_ATTEMPT_P = 0.1973
PAT_ATTEMPT_P = 0.2604

# League make rates, used when the feed serves ``fg`` without ``fga``.
LEAGUE_FG_RATE = 0.8507
LEAGUE_PAT_RATE = 0.9480


@dataclass(frozen=True)
class KickerProjection:
    """One kicker's per-game DK expectation, and the pieces it came from."""

    team: str
    name: str
    player_id: str
    fg_made: float          # FantasyPros ``fg``
    fg_attempts: float      # ``fga``, or ``fg_made / LEAGUE_FG_RATE``
    pat_made: float         # ``xpt``
    fg_rate: float
    rate_source: str        # "projection" (fg/fga) or "league"

    @property
    def points(self) -> float:
        return float(self.fg_made * POINTS_PER_MADE_FG + self.pat_made * PAT_POINTS)


def kicker_rows(fp: pd.DataFrame) -> pd.DataFrame:
    """The kicker rows of a FantasyPros long frame, one wide row per kicker."""
    if fp.empty or "position" not in fp.columns:
        return pd.DataFrame()
    pos = fp["position"].astype(str).str.upper().str.strip()
    k = fp[pos.isin(KICKER_POSITIONS)].copy()
    if k.empty:
        return k
    k["stat"] = k["stat"].astype(str).str.lower()
    k["value"] = pd.to_numeric(k["value"], errors="coerce").fillna(0.0)
    ids = k.groupby("player_id").agg(player_name=("player_name", "first"),
                                     team=("team", "first"))
    wide = k.pivot_table(index="player_id", columns="stat", values="value", aggfunc="mean")
    return ids.join(wide).reset_index()


def project_kickers(fp: pd.DataFrame) -> list[KickerProjection]:
    """One projection per kicker in the FantasyPros frame.

    A kicker with no ``fg`` and no ``xpt`` is skipped rather than priced at
    zero — the point of this module is that a zero is a claim.
    """
    rows = kicker_rows(fp)
    if rows.empty:
        return []

    def number(row: Mapping[Hashable, Any], key: str) -> float:
        """A pivot leaves NaN where a kicker carried no such stat.

        ``value or 0.0`` does NOT handle that: NaN is truthy, so it passes
        straight through and a kicker with no extra-point projection sailed
        past the skip below to be priced at NaN — which is the zero-pricing
        bug this module exists to fix, wearing a different hat.
        """
        value = pd.to_numeric(pd.Series([row.get(key)]), errors="coerce").iloc[0]
        return 0.0 if pd.isna(value) else float(value)

    out: list[KickerProjection] = []
    for r in rows.to_dict("records"):
        made = number(r, "fg")
        attempts = number(r, "fga")
        pat = number(r, "xpt")
        if made <= 0 and pat <= 0:
            continue
        if attempts > made > 0:
            rate, source = made / attempts, "projection"
        else:
            # No attempts served (or a nonsensical pair): fall back to the
            # league rate rather than inventing a perfect kicker.
            rate, source = LEAGUE_FG_RATE, "league"
            attempts = made / LEAGUE_FG_RATE if made > 0 else 0.0
        out.append(KickerProjection(
            team=str(r.get("team", "")), name=str(r.get("player_name", "")),
            player_id=str(r.get("player_id", "")), fg_made=made,
            fg_attempts=attempts, pat_made=pat, fg_rate=rate, rate_source=source,
        ))
    return out


def kicker_expected_points(fp: pd.DataFrame) -> pd.DataFrame:
    """Kicker rows in the scorer's shape: ``[player_id, player_name, team, position, points]``."""
    projections = project_kickers(fp)
    if not projections:
        return pd.DataFrame(columns=[*_ID_COLUMNS, "points"])
    return pd.DataFrame([
        {"player_id": p.player_id, "player_name": p.name, "team": p.team,
         "position": "K", "points": round(p.points, 2)}
        for p in projections
    ])[[*_ID_COLUMNS, "points"]]


def _binomial_count(mean: float, trial_p: float, rng: np.random.Generator,
                    n_sims: int) -> np.ndarray:
    """A count with the given mean whose variance/mean is ``1 - trial_p``."""
    if mean <= 0:
        return np.zeros(n_sims)
    trials = max(int(round(mean / trial_p)), 1)
    return rng.binomial(trials, min(mean / trials, 1.0), n_sims).astype(float)


def kicker_samples(
    fp: pd.DataFrame, rng: np.random.Generator, n_sims: int = 10_000
) -> dict[str, np.ndarray]:
    """Per-sim DK points per kicker, keyed by player name (the GPP hook's key).

    Each simulation draws attempts, then makes on those attempts, then splits
    the makes across DK's distance bands. The band draw matters: a kicker whose
    three makes all land 50+ scores 15 rather than the 10.8 his mean implies,
    and that upper tail is the whole reason a kicker is ever a captain.
    """
    out: dict[str, np.ndarray] = {}
    for p in project_kickers(fp):
        fg_att = _binomial_count(p.fg_attempts, FG_ATTEMPT_P, rng, n_sims)
        fg_made = rng.binomial(fg_att.astype(int), min(p.fg_rate, 1.0)).astype(float)
        pat_att = _binomial_count(
            p.pat_made / LEAGUE_PAT_RATE if p.pat_made > 0 else 0.0,
            PAT_ATTEMPT_P, rng, n_sims,
        )
        pat_made = rng.binomial(pat_att.astype(int), LEAGUE_PAT_RATE).astype(float)
        # Split each sim's made field goals across the bands. rng.multinomial
        # wants one row per draw, so this is the vectorized equivalent.
        bands = np.zeros((n_sims, len(FG_BAND_POINTS)))
        remaining = fg_made.copy()
        share_left = 1.0
        for i, share in enumerate(FG_BAND_SHARE[:-1]):
            q = min(share / share_left, 1.0) if share_left > 0 else 0.0
            drawn = rng.binomial(remaining.astype(int), q).astype(float)
            bands[:, i] = drawn
            remaining -= drawn
            share_left -= share
        bands[:, -1] = remaining
        points = bands @ np.asarray(FG_BAND_POINTS) + pat_made * PAT_POINTS
        out[p.name] = points
    return out
