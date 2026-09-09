"""Slate orchestration — projections + lines → staked, logged bets.

This is the end-to-end wagering path, wiring the pieces together for a slate of
games:

1. Take each game's :class:`~velocity.models.game_nfl.GameProjection` and, for
   every market and side, read the model's probability straight off the
   simulated distribution.
2. Consider only lines observed **before kickoff** (point-in-time correctness —
   never bet a price we could not actually have taken), and **shop** across books
   and numbers, keeping the single best-EV opportunity per market side.
3. **De-vig** each opportunity against its paired opposite side to get the fair
   probability, then measure edge/EV and keep only what clears the threshold.
4. **Stake** survivors with fractional Kelly, capped per bet and per game
   (a game's correlated bets share a group cap).
5. **Log** each bet with its closing line so CLV can be tracked.

The result is a :class:`~velocity.wagering.bet_log.BetLog`; grade it with
``.settle(games)`` for a reproducible bankroll curve.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from velocity.eval.ladders import offset_is_honest
from velocity.models.game_nfl import GameProjection
from velocity.store import pit
from velocity.store.schema import LADDER_BOOKS, contract_key
from velocity.wagering.bet_log import Bet, BetLog
from velocity.wagering.devig import devig
from velocity.wagering.edge import evaluate
from velocity.wagering.fees import venue_for_book
from velocity.wagering.staking import StakingConfig, apply_group_cap, stake_amount

_MARKET_SIDES = {
    "spread": ("home", "away"),
    "total": ("over", "under"),
    "moneyline": ("home", "away"),
    # Team totals: the censored-score derivative (docs/EDGE_RESEARCH.md §2.2).
    # Books derive these linearly from the game total and spread, which ignores
    # that scores are floored at zero; the sim's scores carry the floor, so its
    # prices include exactly the mass the linear derivation misplaces.
    "team_total_home": ("over", "under"),
    "team_total_away": ("over", "under"),
}
_TEAM_TOTAL_MARKETS = ("team_total_home", "team_total_away")
_OPPOSITE = {"home": "away", "away": "home", "over": "under", "under": "over"}


@dataclass(frozen=True)
class SlateConfig:
    """Knobs for building a slate of bets."""

    devig_method: str = "multiplicative"
    min_edge: float = 0.02
    starting_bankroll: float = 100.0
    group_cap_fraction: float = 0.10
    staking: StakingConfig = field(default_factory=StakingConfig)
    # Backtest excludes the closing observation from entry candidates so CLV is
    # measured against a line we did *not* bet. A live slate has no separate close
    # yet (all we have is the current snapshot), so it must keep every observation
    # as a candidate; CLV is measured later against the true closing snapshot.
    exclude_closing: bool = True
    # The E8 ladder gate: refuse rungs where the sim's *shape* is known to be
    # wrong by more than this much probability (docs/BUILD_EXCHANGES.md E8).
    # The sim draws a normal; real residuals are leptokurtic, so it overstates
    # the chance of landing past any threshold — the direction that invents
    # edges rather than hiding them. ``None`` disables the gate entirely.
    ladder_tolerance: float | None = None
    # The league whose calibration table the gate reads. Set by the live
    # runner; without it the gate has no table and stands down.
    league: str | None = None
    # Charge an exchange's per-contract taker fee against EV and stake sizing
    # (docs/BUILD_EXCHANGES.md D3). Sportsbook rows are unaffected either way:
    # their margin is already inside the price. Turn off only to model a
    # maker fill, which pays no fee on Polymarket.
    charge_exchange_fees: bool = True
    # Market anchoring (docs/MODEL_LAB.md Round 3): the close's Brier beats
    # every pure model, so the *belief* probability used for staking may be
    # regressed toward the market's devigged probability:
    #   p_belief = p_market + model_weight · (p_model − p_market)
    # 1.0 = pure model (default, bit-identical to before). The weight is a
    # wagering-policy choice to be set from live paper CLV, not a fit change —
    # leans and cards always keep the pure model.
    model_weight: float = 1.0
    # Confidence calibration: shrink the model's probability toward 0.5 (the
    # no-information point) before measuring edge and staking. 1.0 = the raw model
    # (default, bit-identical to before); < 1.0 tempers an over-confident model so a
    # bet must clear the edge on the *calibrated* probability. The backtest's
    # calibration table is what this is tuned against.
    prob_shrink: float = 1.0
    # Per-market shrink overrides for player props: a market listed here uses its
    # own shrink instead of ``prob_shrink``. The MLB-era prop backtest showed
    # over-confidence varies sharply by market, so the lever is per-market; the
    # football prop backtest re-tunes it. Empty (default) = every market uses
    # ``prob_shrink``. Ignored by the game slate, which has no per-market props.
    prop_shrink_by_market: Mapping[str, float] = field(default_factory=dict)
    # Markets to skip entirely. A market the backtest finds unprofitable at
    # *every* shrink has no confidence lever to rescue it — the honest treatment
    # is to not bet it. Honored by both the game slate and the prop slate
    # (e.g. NCAAF spreads: 50.1% ATS flat, no edge at any disagreement
    # threshold — docs/BACKTEST_NCAAF.md). Empty (default) = bet every market.
    exclude_markets: frozenset[str] = frozenset()
    # Per-market edge thresholds (docs/WAGERING.md Phase W4, DESIGN §6.2): a
    # market listed here clears its own bar instead of ``min_edge`` — wider
    # for the noisier markets, where the same measured edge is more likely to
    # be our own estimation error. Keys are market names as they appear on the
    # board ("total", "pass_yards", …). Empty (default) = every market uses
    # ``min_edge``.
    min_edge_by_market: Mapping[str, float] = field(default_factory=dict)
    # Selectivity on full-game totals, measured in **points of disagreement**
    # rather than probability: bet a total only when the model's fair total
    # differs from the offered number by at least this much, *in the direction
    # of the side being bet*. This is the NCAAF edge as actually backtested
    # (docs/BACKTEST_NCAAF.md): flat totals sit at 51.6%, but the win rate rises
    # monotonically with disagreement — 52.8% at ≥4 points (5,477 bets, positive
    # in 7 of 10 seasons), 53.4% at ≥6. The probability-edge gate (``min_edge``)
    # is a different cut and does not reproduce it, so both are applied.
    #
    # Scope is deliberately the full-game ``total`` only, where the threshold
    # was calibrated. ``0.0`` (default) disables it — every other league is
    # unaffected.
    min_total_disagreement: float = 0.0
    # The same selectivity for team totals (``team_total_home``/``_away``):
    # bet one only when the sim's fair team total (median simulated score,
    # zero-floor included) differs from the number by at least this much in
    # the side's direction. Team totals are the censored-score derivative the
    # market derives linearly (docs/EDGE_RESEARCH.md §2.2 — Arscott 2023
    # measured the bias at >55% over two decades); the threshold keeps only
    # the disagreements big enough to clear vig. ``0.0`` (default) disables.
    min_team_total_disagreement: float = 0.0
    # Paper markets: priced, logged and graded for CLV exactly like everything
    # else, staked at zero. The posture for a market that has no promoted edge
    # yet — NCAAF team totals until the posted-close study sets their gate, a
    # whole league in the content+CLV posture — so the evidence accrues without
    # the money (docs/STRATEGY_REVIEW.md S2). Empty (default) = stake everything
    # that qualifies.
    paper_markets: frozenset[str] = frozenset()
    # Edge ceilings — the adverse-selection guard applied where the money is.
    # On the repo's own graded record the highest-edge quartile carried the
    # worst closing-line value (mean CLV −0.048 vs +0.037 in the lowest;
    # docs/PUBLISH_GATE.md §2): when the model screams, the market usually
    # knows something. The publish gate refused edges above 0.12 on that
    # evidence while the staked slate kept betting them; 60 of the 95 bets on
    # the first NCAAF card sat above it. A row past either ceiling is logged
    # as paper (stake 0, reason in ``Bet.note``) so its CLV still grades the
    # ceiling itself. The relative ceiling is what bites on longshots, where
    # a 0.02 absolute edge is already a quarter of the fair probability.
    # Both default to ``None`` here — this dataclass is the mechanism, and the
    # backtest engine builds it bare; the live runner is the policy and ships
    # 0.12 / 0.50 (``--max-edge`` / ``--max-relative-edge``).
    max_edge: float | None = None
    max_relative_edge: float | None = None
    # What the fair probability is de-vigged from. ``"book"``: the same
    # book's own opposite side — the original behaviour, and a noisy anchor
    # on a lopsided pair (+2400/−5000 at one soft book). ``"consensus"``: the
    # cross-book consensus of both sides at that snapshot, falling back to
    # the book pair where fewer than two books quote the contract. The
    # shopped price is still the best one; only the yardstick it is measured
    # against changes — and it changes conservatively, since the best-priced
    # book's own pair is by construction the one most generous to our side
    # (docs/SYSTEM_REVIEW.md §4.2). The runner ships "consensus".
    devig_anchor: str = "book"

    def shrink_for(self, market: str) -> float:
        """The confidence shrink to apply to ``market`` — its override, else the global."""
        return self.prop_shrink_by_market.get(market, self.prob_shrink)

    def min_edge_for(self, market: str) -> float:
        """The edge threshold for ``market`` — its override, else the global."""
        return float(self.min_edge_by_market.get(market, self.min_edge))

    def paper_reason(self, market: str, edge: float, p_fair: float | None) -> str | None:
        """Why a qualifying bet on ``market`` stays paper, or ``None`` to stake it.

        Checked after the EV gate: the bet is real enough to log and grade;
        this decides whether money follows. A paper market says so first;
        otherwise the absolute and relative ceilings, in that order.
        """
        if market in self.paper_markets or "__all__" in self.paper_markets:
            return "paper market"
        if self.max_edge is not None and edge > self.max_edge:
            return f"edge {edge:.3f} above ceiling {self.max_edge:.2f}"
        if (
            self.max_relative_edge is not None
            and p_fair is not None
            and p_fair > 0.0
            and edge / p_fair > self.max_relative_edge
        ):
            return (f"edge {edge:.3f} is {edge / p_fair:.0%} of fair {p_fair:.3f} "
                    f"(ceiling {self.max_relative_edge:.0%})")
        return None


def total_disagreement(proj: GameProjection, side: str, point: float) -> float:
    """Signed points by which the model's fair total favors ``side`` at ``point``.

    Positive means the model agrees with this side's direction: the over wants
    the model's total above the number, the under wants it below. Mirrors the
    backtest's ``pick_over = fair_total > total_line`` comparison exactly.
    """
    fair = float(proj.fair_total())
    return (fair - point) if side == "over" else (point - fair)


def team_total_disagreement(
    proj: GameProjection, market: str, side: str, point: float
) -> float:
    """Signed points by which the sim's fair team total favors ``side`` at ``point``.

    The fair number is the median simulated score of the market's team — the
    50/50 over point, floor-at-zero included. Same sign convention as
    :func:`total_disagreement`.
    """
    scores = proj.sim.home_score if market == "team_total_home" else proj.sim.away_score
    fair = float(np.median(scores))
    return (fair - point) if side == "over" else (point - fair)


def model_probability(
    proj: GameProjection, market: str, side: str, point: float | None
) -> float | None:
    """The model's probability for one (market, side, point), read off the sim."""
    if market == "moneyline":
        p_home = proj.p_home_win()
        return p_home if side == "home" else 1.0 - p_home
    if point is None:
        raise ValueError(f"{market} requires a point")
    margin = proj.sim.margin
    total = proj.sim.total
    if market == "spread":
        covered = (margin + point) > 0 if side == "home" else (-margin + point) > 0
        return float(np.mean(covered))
    if market == "total":
        hit = total > point if side == "over" else total < point
        return float(np.mean(hit))
    if market in _TEAM_TOTAL_MARKETS:
        scores = proj.sim.home_score if market == "team_total_home" else proj.sim.away_score
        hit = scores > point if side == "over" else scores < point
        return float(np.mean(hit))
    raise ValueError(f"unknown market {market!r}")


def _ladder_gate_blocks(
    proj: GameProjection,
    market: str,
    side: str,
    point: float | None,
    book: str,
    config: SlateConfig,
) -> bool:
    """Whether this rung sits where the sim's distribution shape is untrustworthy.

    The offset is measured from the model's own fair line, because that is what
    the calibration measures: how badly a normal misses this far out from the
    expectation. Spread points are normalized to the home side first — a fair
    spread of −6 is quoted as away +6, so comparing the away number to the home
    fair line directly would read twelve points of offset where there are none.
    Only :data:`~velocity.store.schema.LADDER_BOOKS` rows are gated. A
    sportsbook posts one main number that its own backtests already validate,
    and gating it would silently switch off ordinary spread and total betting;
    an exchange posts the whole ladder, which is what this measurement is
    about. A market with no number, no configured league, or no tolerance set
    is never blocked.
    """
    if config.ladder_tolerance is None or config.league is None or point is None:
        return False
    if str(book).lower() not in LADDER_BOOKS:
        return False
    if market == "spread":
        home_point = contract_key(market, side, point)
        if home_point is None:
            return False
        offset = abs(home_point - float(proj.sim.fair_spread()))
    elif market == "total":
        offset = abs(point - float(proj.sim.fair_total()))
    elif market in _TEAM_TOTAL_MARKETS:
        scores = proj.sim.home_score if market == "team_total_home" else proj.sim.away_score
        offset = abs(point - float(np.median(scores)))
    else:
        return False
    return not offset_is_honest(
        config.league, market, offset, tolerance=config.ladder_tolerance
    )


def consensus_snapshots(
    snapshots: Mapping[tuple, Mapping[str, tuple[float, float | None]]],
    min_books: int = 2,
) -> dict[tuple, dict[str, tuple[float, float | None]]]:
    """Cross-book consensus buckets, keyed like ``snapshots`` minus the book.

    For every ``(market, timestamp, contract)`` quoted by at least ``min_books``
    books, each side's price is the consensus in decimal space
    (:func:`~velocity.wagering.odds.consensus_american`); the point is the
    contract's own. A contract one book quotes alone has no consensus and is
    absent — the caller falls back to that book's pair.
    """
    from velocity.wagering.odds import consensus_american

    grouped: dict[tuple, dict[str, list[tuple[float, float | None]]]] = {}
    for (market, _book, stamp, contract), sides in snapshots.items():
        target = grouped.setdefault((market, stamp, contract), {})
        for side, quote in sides.items():
            target.setdefault(side, []).append(quote)
    out: dict[tuple, dict[str, tuple[float, float | None]]] = {}
    for key, sides in grouped.items():
        bucket: dict[str, tuple[float, float | None]] = {}
        for side, quotes in sides.items():
            if len(quotes) < min_books:
                continue
            price = consensus_american([q[0] for q in quotes])
            if price is None:
                continue
            bucket[side] = (float(price), quotes[0][1])
        if bucket:
            out[key] = bucket
    return out


def _fair_probability(
    bucket: Mapping[str, tuple[float, float | None]],
    side: str,
    method: str,
) -> float | None:
    """De-vig our side against its paired opposite in the same market snapshot."""
    opp = _OPPOSITE[side]
    if side not in bucket or opp not in bucket:
        return None
    our_price = bucket[side][0]
    opp_price = bucket[opp][0]
    fair = devig([our_price, opp_price], method=method)
    return fair[0]


def build_slate(
    projections: dict[str, GameProjection],
    lines: pd.DataFrame,
    games: pd.DataFrame,
    config: SlateConfig | None = None,
) -> BetLog:
    """Build a :class:`BetLog` of staked bets from projections and a line archive."""
    config = config or SlateConfig()
    pre = pit.lines_before_kickoff(lines, games)
    closing = pit.closing_line(lines, games)
    # Backtest excludes the closing observation so CLV is measured against a line
    # we did *not* bet (else "betting the close" reports ~0 CLV). A live snapshot
    # is the only board, so nothing is excluded — CLV is measured later, against
    # the real closing snapshot from the archive.
    excluded = set(closing["line_id"]) if config.exclude_closing else set()
    entries = pre[~pre["line_id"].isin(excluded)]
    log = BetLog()

    for game_id, proj in projections.items():
        game_lines = entries[entries["game_id"] == game_id]
        if game_lines.empty:
            continue

        # For de-vig we need both sides at the same (book, timestamp); index them.
        snapshots: dict[tuple, dict[str, tuple[float, float | None]]] = {}
        for row in game_lines.to_dict("records"):
            point = None if pd.isna(row["point"]) else float(row["point"])
            key = (
                row["market"],
                row["book"],
                row["timestamp"],
                contract_key(row["market"], row["side"], point),
            )
            snapshots.setdefault(key, {})[row["side"]] = (float(row["price"]), point)

        consensus = consensus_snapshots(snapshots) if config.devig_anchor == "consensus" else {}

        game_stakes: dict[str, float] = {}
        pending: dict[str, dict] = {}

        for market, sides in _MARKET_SIDES.items():
            if market in config.exclude_markets:  # no edge found → not bet
                continue
            for side in sides:
                best = _best_opportunity(
                    game_lines, snapshots, proj, market, side, config, consensus
                )
                if best is None:
                    continue
                paper = config.paper_reason(market, best["edge"], best.get("p_fair"))
                stake = stake_amount(
                    config.starting_bankroll,
                    best["p_model"],
                    best["price"],
                    config.staking,
                    venue_for_book(best["book"]) if config.charge_exchange_fees else None,
                )
                if stake <= 0.0 and paper is None:
                    continue
                bet_key = f"{market}:{side}"
                if paper is None:
                    game_stakes[bet_key] = stake
                pending[bet_key] = {**best, "note": paper}

        if not pending:
            continue

        # Correlated bets on one game share a group cap; paper rows carry no
        # stake and take no share of it.
        capped = apply_group_cap(game_stakes, config.group_cap_fraction, config.starting_bankroll)
        for bet_key, info in pending.items():
            stake = capped.get(bet_key, 0.0)
            if stake <= 0.0 and info.get("note") is None:
                continue
            close = _closing_for(
                closing,
                game_id,
                info["market"],
                info["side"],
                info["book"],
                info.get("point"),
            )
            log.add(
                Bet(
                    game_id=game_id,
                    market=info["market"],
                    side=info["side"],
                    book=info["book"],
                    price=info["price"],
                    stake=stake,
                    p_model=info["p_model"],
                    point=info["point"],
                    timestamp=info["timestamp"],
                    closing_price=close[0] if close else None,
                    closing_point=close[1] if close else None,
                    p_fair=info.get("p_fair"),
                    note=info.get("note"),
                )
            )

    return log


def _best_opportunity(
    game_lines: pd.DataFrame,
    snapshots: dict[tuple, dict[str, tuple[float, float | None]]],
    proj: GameProjection,
    market: str,
    side: str,
    config: SlateConfig,
    consensus: Mapping[tuple, Mapping[str, tuple[float, float | None]]] | None = None,
) -> dict | None:
    """Highest-EV qualifying opportunity for one market side (shops book/number/time).

    ``consensus`` (from :func:`consensus_snapshots`) supplies the fair-probability
    anchor when the config asks for it and at least two books quote both sides
    of the contract; otherwise the book's own pair is the anchor.
    """
    candidates = game_lines[(game_lines["market"] == market) & (game_lines["side"] == side)]
    best: dict | None = None
    for row in candidates.to_dict("records"):
        point = None if pd.isna(row["point"]) else float(row["point"])
        contract = contract_key(market, side, point)
        bucket = snapshots.get((market, row["book"], row["timestamp"], contract), {})
        p_fair = None
        if consensus:
            wide = consensus.get((market, row["timestamp"], contract))
            if wide is not None:
                p_fair = _fair_probability(wide, side, config.devig_method)
        if p_fair is None:
            p_fair = _fair_probability(bucket, side, config.devig_method)
        if p_fair is None:
            continue
        # Points-of-disagreement selectivity on full-game totals (the backtested
        # NCAAF cut). Applied before pricing: a number the model barely disagrees
        # with is not a candidate at any price.
        if (
            market == "total"
            and config.min_total_disagreement > 0.0
            and point is not None
            and total_disagreement(proj, side, point) < config.min_total_disagreement
        ):
            continue
        if (
            market in _TEAM_TOTAL_MARKETS
            and config.min_team_total_disagreement > 0.0
            and point is not None
            and team_total_disagreement(proj, market, side, point)
            < config.min_team_total_disagreement
        ):
            continue
        venue = venue_for_book(row["book"]) if config.charge_exchange_fees else None
        if _ladder_gate_blocks(proj, market, side, point, row["book"], config):
            continue
        p_model = model_probability(proj, market, side, point)
        if p_model is None:  # projection can't price this market (no segment sim)
            continue
        if config.prob_shrink != 1.0:  # temper an over-confident model toward 0.5
            p_model = 0.5 + config.prob_shrink * (p_model - 0.5)
        if config.model_weight != 1.0:  # market anchoring (Round 3): regress the
            # staking belief toward this line's own devigged probability.
            p_model = p_fair + config.model_weight * (p_model - p_fair)
        signal = evaluate(
            p_model,
            float(row["price"]),
            p_fair,
            min_edge=config.min_edge_for(market),
            venue=venue,
        )
        if not signal.qualifies:
            continue
        if best is None or signal.ev > best["ev"]:
            best = {
                "market": market,
                "side": side,
                "book": row["book"],
                "price": float(row["price"]),
                "point": point,
                "timestamp": row["timestamp"],
                "p_model": p_model,
                "p_fair": p_fair,
                "edge": signal.edge,
                "ev": signal.ev,
            }
    return best


def _closing_for(
    closing: pd.DataFrame,
    game_id: str,
    market: str,
    side: str,
    book: str,
    point: float | None = None,
) -> tuple[float, float | None] | None:
    """The closing price/point for a market side, preferring the same book.

    A bet struck on a :data:`~velocity.store.schema.LADDER_BOOKS` venue is
    closed out against **its own rung**: there every number is a separate
    contract, so the loose match a sportsbook needs would hand a −20.5 bet the
    −1.5 rung's price and report ~19 points of CLV that never existed. A
    sportsbook bet keeps the loose match, because its close is the same market
    wherever the number moved to — which is what ``line_clv`` exists to measure.
    """
    match = closing[
        (closing["game_id"] == game_id)
        & (closing["market"] == market)
        & (closing["side"] == side)
    ]
    if str(book).lower() in LADDER_BOOKS:
        match = match[match["book"].astype(str).str.lower().isin(LADDER_BOOKS)]
        match = (
            match[match["point"].isna()] if point is None else match[match["point"] == point]
        )
    if match.empty:
        return None
    same_book = match[match["book"] == book]
    row = (same_book if not same_book.empty else match).iloc[-1]
    close_point = None if pd.isna(row["point"]) else float(row["point"])
    return float(row["price"]), close_point
