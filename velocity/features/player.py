"""Player usage features — shares, empirical-Bayes shrinkage, injury repricing.

Player props decompose as ``team volume × player share × efficiency × game
script`` (DESIGN §5). This module owns the **share** term — how a team's targets
or carries divide among its players — and the two disciplines that make it
trustworthy:

* **Usage is stickier than efficiency**, so we weight recent role heavily but
  still shrink small samples. A back with three carries has not earned a 100%
  share estimate; empirical-Bayes pulls thin samples toward a role baseline with
  a pseudo-count prior.
* **Injuries reprice the whole room.** A scratched starter's share does not
  vanish — it redistributes to teammates. :func:`redistribute_shares` moves an
  inactive player's share proportionally onto the active ones so the room still
  sums to one.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


def usage_shares(counts: pd.Series) -> pd.Series:
    """Normalize raw usage counts into shares that sum to 1.

    ``counts`` maps player → usage (targets, carries, …). An all-zero input
    returns all zeros rather than dividing by zero.
    """
    total = counts.sum()
    if total == 0:
        return counts.astype(float) * 0.0
    return counts / total


def empirical_bayes_share(
    counts: pd.Series,
    *,
    prior_strength: float = 10.0,
    prior_share: pd.Series | None = None,
) -> pd.Series:
    """Shrink observed usage shares toward a role baseline with a pseudo-count prior.

    Each player's shrunk share is computed as if ``prior_strength`` pseudo-events
    were distributed according to ``prior_share`` (a uniform baseline by default)
    and added to their observed ``counts`` before normalizing. Small samples
    regress toward the baseline; large samples barely move. The result sums to 1.
    """
    n = len(counts)
    if n == 0:
        return counts.astype(float)
    if prior_share is None:
        prior_share = pd.Series(1.0 / n, index=counts.index)
    else:
        prior_share = prior_share.reindex(counts.index).fillna(0.0)
        prior_share = usage_shares(prior_share)

    augmented = counts + prior_strength * prior_share
    return usage_shares(augmented)


def redistribute_shares(shares: pd.Series, inactive: Iterable[str]) -> pd.Series:
    """Reprice a share room after scratching ``inactive`` players.

    The inactive players are zeroed and the remaining shares renormalized so the
    active room still sums to 1 (preserving their relative order). Scratching a
    starter therefore lifts every remaining teammate.
    """
    inactive = set(inactive)
    active = shares.copy()
    active[active.index.isin(inactive)] = 0.0
    remaining = active.sum()
    if remaining == 0:
        return active
    return active / remaining
