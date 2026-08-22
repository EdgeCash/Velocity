"""GPP portfolio construction — many diversified, stacked lineups, not one.

The cash optimizer (:mod:`velocity.dfs.optimizer`) maximizes projected
points; tournaments pay the *right tail* of a huge field, and the
peer-reviewed record says a single lineup is the wrong unit entirely
(docs/EDGE_RESEARCH.md §5): Hunter/Vielma/Zaman measured a single lineup's
median tournament outcome at −100% and showed diversified portfolios with
pairwise-overlap caps and stacking constraints shift the whole distribution;
the 452-lineup Milly-Maker winners study pins the shape that actually wins —
QB + 2 teammates + a bring-back, salary deliberately left unspent (full-cap
builds duplicate hundreds of times).

The builder encodes those findings directly:

* **Candidates** come from re-solving the exact knapsack under seeded
  multiplicative projection jitter (the practitioner "randomness" pattern) at
  an effective cap of ``SALARY_CAP − salary_leave``.
* **Stacks** are enforced structurally: a rostered QB needs
  ``stack_teammates`` same-team pass-catchers and ``bring_backs`` opponents
  (opponents derived from the salary board's competition strings).
* **Selection** is greedy under a pairwise overlap cap and a per-player
  exposure cap, scored by the **tail** of the correlated simulation when
  per-player sample arrays are supplied (GPPs pay the tail, not the mean) and
  by true projected points otherwise.

Everything is deterministic under a seeded generator — same inputs, same
portfolio.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from velocity.dfs.optimizer import (
    NFL_CLASSIC,
    SALARY_CAP,
    Lineup,
    LineupSlot,
    RosterSpec,
    build_lineup,
)

# Positions that count as a QB's stacking partners (his pass-catchers, plus
# the RB — dump-offs and game-script correlation).
_STACK_POSITIONS = ("WR", "TE", "RB")


@dataclass(frozen=True)
class GppConfig:
    """Portfolio knobs, defaulted to the documented winning shapes."""

    n_lineups: int = 20
    # Candidates generated per requested lineup before selection.
    candidate_factor: int = 8
    # Multiplicative projection noise (sd). ~5–10% is the practitioner norm:
    # enough to explore the near-optimal frontier, not enough to roster chalk
    # the projections hate.
    jitter: float = 0.08
    # Max players any two chosen lineups may share (Hunter et al.: 4–7,
    # tighter on bigger slates).
    max_overlap: int = 5
    # Max fraction of the portfolio any one player appears in.
    max_exposure: float = 0.6
    # Effective cap = SALARY_CAP − salary_leave: full-cap lineups duplicate
    # hundreds of times in large fields; leaving salary is dup-avoidance.
    salary_leave: int = 200
    # Structural stack: QB + ≥stack_teammates same-team partners and
    # ≥bring_backs opponents from the QB's game.
    require_stack: bool = True
    stack_teammates: int = 2
    bring_backs: int = 1
    # Tail objective: mean lineup score across the sims at or above this
    # quantile of the lineup's own distribution.
    tail_q: float = 0.85


def opponent_map(pool: pd.DataFrame) -> dict[str, str]:
    """``team → opponent`` from the salary board's competition strings.

    Two teams sharing one competition are opponents; competitions that don't
    resolve to exactly two teams (missing data, showdown oddities) contribute
    nothing — a bring-back is then simply not required for that game.
    """
    if "competition" not in pool.columns or "team" not in pool.columns:
        return {}
    out: dict[str, str] = {}
    span = pool.dropna(subset=["competition", "team"])
    for _, part in span.groupby("competition"):
        teams = sorted(set(part["team"].astype(str)))
        if len(teams) == 2:
            out[teams[0]] = teams[1]
            out[teams[1]] = teams[0]
    return out


def stack_ok(lineup: Lineup, opponents: Mapping[str, str], config: GppConfig) -> bool:
    """Does the lineup carry the documented GPP stack shape?

    Checked for every rostered QB with a known team: ``stack_teammates``
    same-team partners are required always; ``bring_backs`` opponents only
    when the QB's opponent is known from the board. A lineup with no QB (or
    no team data at all) passes — the rule can't apply.
    """
    qbs = [s for s in lineup.slots if s.position == "QB" and s.team]
    if not qbs:
        return True
    for qb in qbs:
        mates = sum(
            1 for s in lineup.slots
            if s.team == qb.team and s.position in _STACK_POSITIONS
        )
        if mates < config.stack_teammates:
            return False
        opponent = opponents.get(str(qb.team))
        if opponent is not None:
            backs = sum(1 for s in lineup.slots if s.team == opponent)
            if backs < config.bring_backs:
                return False
    return True


def _players(lineup: Lineup) -> frozenset[str]:
    return frozenset(s.player_name for s in lineup.slots)


def reprice(lineup: Lineup, points_by_name: Mapping[str, float]) -> Lineup:
    """The same roster with **true** projected points (candidates carry jitter)."""
    slots = tuple(
        LineupSlot(
            slot=s.slot, player_name=s.player_name, position=s.position,
            team=s.team, salary=s.salary,
            points=round(float(points_by_name.get(s.player_name, s.points)), 2),
        )
        for s in lineup.slots
    )
    return Lineup(
        slots=slots,
        total_salary=lineup.total_salary,
        total_points=round(float(sum(s.points for s in slots)), 2),
    )


def tail_score(
    lineup: Lineup,
    samples: Mapping[str, np.ndarray],
    *,
    tail_q: float = 0.85,
) -> float:
    """Mean of the lineup's simulated totals at or above its ``tail_q`` quantile.

    ``samples`` maps player names to per-sim fantasy-point arrays drawn from
    the *same* simulated games, so teammate correlation flows into the lineup
    total for free — which is the entire point: a stacked lineup's tail is
    fatter than its mean suggests, and this score sees it. Players without a
    sample array contribute their (constant) projected points.
    """
    arrays = [np.asarray(samples[s.player_name], dtype=float)
              for s in lineup.slots if s.player_name in samples]
    constant = sum(s.points for s in lineup.slots if s.player_name not in samples)
    if not arrays:
        return float(constant)
    totals = np.sum(arrays, axis=0) + constant
    cut = float(np.quantile(totals, tail_q))
    tail = totals[totals >= cut]
    return float(tail.mean())


@dataclass(frozen=True)
class GppPortfolio:
    """The chosen lineups plus the pool-level accounting."""

    lineups: tuple[Lineup, ...]
    scores: tuple[float, ...]  # selection scores, aligned with lineups
    n_candidates: int  # distinct legal candidates the jitter produced
    n_stacked: int  # candidates surviving the stack rule


def build_gpp_portfolio(
    pool: pd.DataFrame,
    *,
    spec: RosterSpec = NFL_CLASSIC,
    samples: Mapping[str, np.ndarray] | None = None,
    config: GppConfig | None = None,
    rng: np.random.Generator,
) -> GppPortfolio:
    """Generate, filter, score, and select a GPP portfolio from a player pool.

    ``pool`` is the cash optimizer's input (``lineup_pool`` output — salaries
    joined to projections, with ``team``/``competition`` when available).
    ``samples`` optionally maps player names to correlated per-sim
    fantasy-point arrays; with it, selection scores the tail, without it,
    true projected points. Deterministic for a seeded ``rng``.
    """
    config = config or GppConfig()
    pool = pool.dropna(subset=["salary", "points"]).reset_index(drop=True)
    if pool.empty:
        return GppPortfolio((), (), 0, 0)
    points_by_name = dict(zip(pool["player_name"], pool["points"].astype(float),
                              strict=False))
    opponents = opponent_map(pool)
    cap = SALARY_CAP - max(0, config.salary_leave)

    seen: set[frozenset[str]] = set()
    candidates: list[Lineup] = []
    n_stacked = 0
    for _ in range(max(1, config.candidate_factor) * config.n_lineups):
        jittered = pool.copy()
        noise = rng.normal(1.0, config.jitter, len(jittered))
        jittered["points"] = (jittered["points"].astype(float) * np.clip(noise, 0.0, None))
        lineup = build_lineup(jittered, cap=cap, spec=spec)
        if lineup is None:
            continue
        roster = _players(lineup)
        if roster in seen:
            continue
        seen.add(roster)
        if config.require_stack and not stack_ok(lineup, opponents, config):
            continue
        n_stacked += 1
        candidates.append(reprice(lineup, points_by_name))

    if not candidates:
        return GppPortfolio((), (), len(seen), 0)

    if samples:
        scored = [(tail_score(lu, samples, tail_q=config.tail_q), lu) for lu in candidates]
    else:
        scored = [(lu.total_points, lu) for lu in candidates]
    # Deterministic order: score desc, then roster names as the tiebreak.
    scored.sort(key=lambda pair: (-pair[0], tuple(sorted(_players(pair[1])))))

    max_count = max(1, int(np.floor(config.max_exposure * config.n_lineups)))
    chosen: list[tuple[float, Lineup]] = []
    exposure: Counter[str] = Counter()
    for score, lineup in scored:
        if len(chosen) >= config.n_lineups:
            break
        roster = _players(lineup)
        if any(len(roster & _players(other)) > config.max_overlap for _, other in chosen):
            continue
        if any(exposure[name] + 1 > max_count for name in roster):
            continue
        chosen.append((score, lineup))
        exposure.update(roster)

    return GppPortfolio(
        lineups=tuple(lu for _, lu in chosen),
        scores=tuple(round(score, 3) for score, _ in chosen),
        n_candidates=len(seen),
        n_stacked=n_stacked,
    )


def portfolio_frame(portfolio: GppPortfolio) -> pd.DataFrame:
    """One row per lineup: roster, salary, projection, score, and stack callouts."""
    rows = []
    for rank, (lineup, score) in enumerate(
        zip(portfolio.lineups, portfolio.scores, strict=True), start=1
    ):
        rows.append({
            "rank": rank,
            "players": " | ".join(
                f"{s.slot}:{s.player_name}" for s in lineup.slots
            ),
            "total_salary": lineup.total_salary,
            "total_points": lineup.total_points,
            "score": score,
            "stacks": "; ".join(lineup.stacks()),
        })
    return pd.DataFrame(
        rows, columns=["rank", "players", "total_salary", "total_points", "score", "stacks"]
    )
