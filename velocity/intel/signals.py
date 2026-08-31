"""Context signals — each judges one qualifying bet against the game's evidence.

A signal answers one narrow question ("does the unit matchup agree with this
side?", "is anyone load-bearing out?") and returns a score in ``[-1, +1]``
**aligned with the bet**: positive confirms the side being bet, negative
contradicts it, and ``None`` means the signal abstains (no data, or nothing to
say about this market). A signal may also **veto** — flag the bet as
unplayable on information the pricing model cannot see (a starting QB on the
injury report, the prop's own player ruled out).

Signals never touch probabilities, edges, or stakes. The EV gate has already
run; this layer only confirms, contradicts, or vetoes what cleared it. Scores
are deliberately z-scored against the league's cross-team spread (the same
``ADV`` discipline as the deep-dive card) so a coin-flip difference reads as
~0, not as conviction.

Weather is deliberately absent: wind is already priced *inside* the projection
(``WeatherAdjustedModel``), and a signal repeating it would double-count.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from velocity.intel.context import GameContext, InjuryOut, TeamContext
from velocity.wagering.bet_log import Bet

_TEAM_SIDES = frozenset({"home", "away"})
_TOTAL_SIDES = frozenset({"over", "under"})


@dataclass(frozen=True)
class SignalResult:
    """One signal's verdict on one bet."""

    name: str
    score: float  # [-1, +1], positive = confirms the side being bet
    rationale: str  # one human-readable sentence of evidence
    veto: bool = False  # True: information the model can't see makes this unplayable


def _clip(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _total_sign(side: str) -> float:
    return 1.0 if side == "over" else -1.0


# ---------------------------------------------------------------------------
# Matchup — unit strength vs unit strength, the model's own currencies.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchupSignal:
    """Season unit quality: EPA/play edges where plays exist, scoring form otherwise."""

    name: str = "matchup"

    def evaluate(self, bet: Bet, ctx: GameContext) -> SignalResult | None:
        if bet.side in _TEAM_SIDES and bet.market in ("spread", "moneyline"):
            picked, opp = ctx.picked(bet.side)
            return self._team_edge(picked, opp, ctx)
        if bet.side in _TOTAL_SIDES and bet.market == "total":
            return self._total_environment(bet.side, ctx)
        return None

    def _team_edge(
        self, picked: TeamContext, opp: TeamContext, ctx: GameContext
    ) -> SignalResult | None:
        epa_values = (picked.off_epa, opp.off_epa, picked.def_epa, opp.def_epa)
        if None not in epa_values:
            zs = (
                ctx.zscore("off_epa", float(picked.off_epa)),  # type: ignore[arg-type]
                ctx.zscore("off_epa", float(opp.off_epa)),  # type: ignore[arg-type]
                # Defensive EPA is EPA *allowed*: the picked side wants the opponent leaky.
                ctx.zscore("def_epa", float(opp.def_epa)),  # type: ignore[arg-type]
                ctx.zscore("def_epa", float(picked.def_epa)),  # type: ignore[arg-type]
            )
            if None not in zs:
                p_off, o_off, o_def, p_def = (float(z) for z in zs)  # type: ignore[arg-type]
                z = ((p_off - o_off) + (o_def - p_def)) / 2.0
                return SignalResult(
                    self.name,
                    _clip(z),
                    f"unit EPA edge {z:+.1f}σ toward {picked.team} "
                    f"(off {picked.off_epa:+.3f} vs {opp.off_epa:+.3f}, "
                    f"def allowed {picked.def_epa:+.3f} vs {opp.def_epa:+.3f})",
                )
        if None not in (picked.ppg, opp.ppg, picked.papg, opp.papg):
            pick_net = float(picked.ppg) - float(picked.papg)  # type: ignore[arg-type]
            opp_net = float(opp.ppg) - float(opp.papg)  # type: ignore[arg-type]
            spread = ctx.dispersion.get("net_ppg")
            if spread is None or spread <= 0:
                return None
            z = (pick_net - opp_net) / (2.0 * spread)
            return SignalResult(
                self.name,
                _clip(z),
                f"scoring-form edge {z:+.1f}σ toward {picked.team} "
                f"(net {pick_net:+.1f} vs {opp_net:+.1f} pts/gm)",
            )
        return None

    def _total_environment(self, side: str, ctx: GameContext) -> SignalResult | None:
        values = (
            ("off_epa", ctx.away.off_epa),
            ("off_epa", ctx.home.off_epa),
            ("def_epa", ctx.away.def_epa),  # leaky defenses raise the environment
            ("def_epa", ctx.home.def_epa),
        )
        zs = [ctx.zscore(stat, float(v)) for stat, v in values if v is not None]
        clean = [z for z in zs if z is not None]
        if len(clean) < len(values):
            return None
        env = sum(clean) / len(clean)
        z = env * _total_sign(side)
        direction = "high" if env > 0 else "low"
        return SignalResult(
            self.name,
            _clip(z),
            f"scoring environment {env:+.1f}σ ({direction}-scoring units on both sides)",
        )


# ---------------------------------------------------------------------------
# Form — is recent performance ahead of or behind the season baseline?
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormSignal:
    """Recent scoring vs season baseline: rewards teams trending toward the bet."""

    name: str = "form"
    window_label: str = "last 5"

    def evaluate(self, bet: Bet, ctx: GameContext) -> SignalResult | None:
        spread = ctx.dispersion.get("net_ppg")
        if spread is None or spread <= 0:
            return None
        if bet.side in _TEAM_SIDES and bet.market in ("spread", "moneyline"):
            picked, opp = ctx.picked(bet.side)
            trends = [self._trend(t) for t in (picked, opp)]
            if trends[0] is None or trends[1] is None:
                return None
            z = (trends[0] - trends[1]) / (2.0 * spread)
            streak = f" ({picked.streak})" if picked.streak else ""
            return SignalResult(
                self.name,
                _clip(z),
                f"{picked.team} {self.window_label}: {trends[0]:+.1f} net pts/gm vs season"
                f"{streak}; {opp.team} {trends[1]:+.1f}",
            )
        if bet.side in _TOTAL_SIDES and bet.market == "total":
            combined = [self._total_trend(t) for t in (ctx.away, ctx.home)]
            if combined[0] is None or combined[1] is None:
                return None
            trend = (combined[0] + combined[1]) / 2.0
            z = trend / (2.0 * spread) * _total_sign(bet.side)
            return SignalResult(
                self.name,
                _clip(z),
                f"recent game totals {trend:+.1f} pts/gm vs season baseline",
            )
        return None

    @staticmethod
    def _trend(team: TeamContext) -> float | None:
        recent, season = team.recent_net_ppg(), team.net_ppg()
        if recent is None or season is None:
            return None
        return recent - season

    @staticmethod
    def _total_trend(team: TeamContext) -> float | None:
        if None in (team.recent_ppg, team.recent_papg, team.ppg, team.papg):
            return None
        recent = float(team.recent_ppg) + float(team.recent_papg)  # type: ignore[arg-type]
        season = float(team.ppg) + float(team.papg)  # type: ignore[arg-type]
        return recent - season


# ---------------------------------------------------------------------------
# Rest — schedule spots the ratings fit sees only partially.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RestSignal:
    """Rest-day differential on team sides; abstains under ``min_gap_days``."""

    name: str = "rest"
    min_gap_days: int = 3
    max_normal_rest: int = 14  # both sides beyond this = openers/byes-for-all, no signal

    def evaluate(self, bet: Bet, ctx: GameContext) -> SignalResult | None:
        if bet.side not in _TEAM_SIDES or bet.market not in ("spread", "moneyline"):
            return None
        picked, opp = ctx.picked(bet.side)
        if picked.rest_days is None or opp.rest_days is None:
            return None
        if picked.rest_days > self.max_normal_rest and opp.rest_days > self.max_normal_rest:
            return None
        gap = picked.rest_days - opp.rest_days
        if abs(gap) < self.min_gap_days:
            return None
        return SignalResult(
            self.name,
            _clip(gap / 7.0),
            f"rest edge: {picked.team} {picked.rest_days}d vs {opp.team} {opp.rest_days}d",
        )


# ---------------------------------------------------------------------------
# Injuries — the one input the pricing model genuinely cannot see.
# ---------------------------------------------------------------------------

# Positional weight of a genuine out, in rough points-of-impact currency.
# QB dominates by design (DESIGN.md §4.2: "QB is the largest single factor").
_OFFENSE_WEIGHTS: Mapping[str, float] = {
    "QB": 0.9,
    "RB": 0.3,
    "WR": 0.3,
    "TE": 0.25,
    "FB": 0.1,
    "OL": 0.25,
    "T": 0.25,
    "G": 0.2,
    "C": 0.25,
    "OT": 0.25,
    "OG": 0.2,
}
_DEFENSE_WEIGHTS: Mapping[str, float] = {
    "DE": 0.25,
    "DT": 0.2,
    "NT": 0.15,
    "EDGE": 0.25,
    "LB": 0.2,
    "ILB": 0.2,
    "OLB": 0.2,
    "MLB": 0.2,
    "CB": 0.25,
    "S": 0.2,
    "SS": 0.2,
    "FS": 0.2,
    "DB": 0.2,
    "DL": 0.2,
}


def _burdens(team: TeamContext) -> tuple[float, float, list[InjuryOut]]:
    """``(offense burden, defense burden, weighted outs)`` for one team's report."""
    off = 0.0
    dfn = 0.0
    counted: list[InjuryOut] = []
    for out in team.outs:
        pos = out.position.upper()
        if pos in _OFFENSE_WEIGHTS:
            off += _OFFENSE_WEIGHTS[pos]
            counted.append(out)
        elif pos in _DEFENSE_WEIGHTS:
            dfn += _DEFENSE_WEIGHTS[pos]
            counted.append(out)
    return off, dfn, counted


def _outs_text(team: TeamContext) -> str:
    names = [f"{o.player_name} ({o.position})" for o in team.outs[:4] if o.player_name]
    return ", ".join(names) if names else "none listed"


def _qb_out(team: TeamContext) -> InjuryOut | None:
    for out in team.outs:
        if out.position.upper() == "QB":
            return out
    return None


@dataclass(frozen=True)
class InjurySignal:
    """Weights genuine outs by position; vetoes a side whose QB is out.

    The QB veto exists because the ratings fit prices the *most recently
    observed* starter — a QB ruled out this week is exactly the information
    the model has not seen yet, and QB is the largest single factor in the
    game (DESIGN.md §4.2). Abstains entirely when no snapshot was supplied.
    """

    name: str = "injury"
    burden_scale: float = 1.5  # burden sum that saturates the score
    qb_veto: bool = True

    def evaluate(self, bet: Bet, ctx: GameContext) -> SignalResult | None:
        if not ctx.away.outs and not ctx.home.outs:
            return None
        if bet.side in _TEAM_SIDES and bet.market in ("spread", "moneyline"):
            picked, opp = ctx.picked(bet.side)
            p_off, p_def, _ = _burdens(picked)
            o_off, o_def, _ = _burdens(opp)
            net = (o_off + o_def) - (p_off + p_def)
            qb = _qb_out(picked)
            if qb is not None and self.qb_veto:
                return SignalResult(
                    self.name,
                    -1.0,
                    f"{picked.team} QB {qb.player_name or 'starter'} is "
                    f"{qb.status or 'out'} — the fit priced the healthy starter",
                    veto=True,
                )
            return SignalResult(
                self.name,
                _clip(net / self.burden_scale),
                f"injury burden favors {picked.team if net > 0 else opp.team} "
                f"({picked.team} out: {_outs_text(picked)}; "
                f"{opp.team} out: {_outs_text(opp)})",
            )
        if bet.side in _TOTAL_SIDES and bet.market == "total":
            a_off, a_def, _ = _burdens(ctx.away)
            h_off, h_def, _ = _burdens(ctx.home)
            # Missing defenders raise the environment; missing offense lowers it.
            env = (a_def + h_def) - (a_off + h_off)
            z = env / self.burden_scale * _total_sign(bet.side)
            lean = "over" if env > 0 else "under"
            return SignalResult(
                self.name,
                _clip(z),
                f"outs lean {lean} ({ctx.away.team}: {_outs_text(ctx.away)}; "
                f"{ctx.home.team}: {_outs_text(ctx.home)})",
            )
        return None


# ---------------------------------------------------------------------------
# Props — availability and the opposing unit the stat runs through.
# ---------------------------------------------------------------------------


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z ]", "", name.casefold()).strip()


def _match_out(player: str, outs: tuple[InjuryOut, ...]) -> InjuryOut | None:
    """The report entry for ``player``, matched exactly else by last name + initial."""
    key = _name_key(player)
    if not key:
        return None
    for out in outs:
        if _name_key(out.player_name) == key:
            return out
    parts = key.split()
    if len(parts) < 2:
        return None
    last, initial = parts[-1], parts[0][0]
    for out in outs:
        theirs = _name_key(out.player_name).split()
        if len(theirs) >= 2 and theirs[-1] == last and theirs[0][0] == initial:
            return out
    return None


@dataclass(frozen=True)
class PropAvailabilitySignal:
    """Vetoes a prop whose own player sits on the injury report as out."""

    name: str = "availability"

    def evaluate(self, bet: Bet, ctx: GameContext) -> SignalResult | None:
        if bet.player is None:
            return None
        for team in (ctx.away, ctx.home):
            hit = _match_out(bet.player, team.outs)
            if hit is not None:
                return SignalResult(
                    self.name,
                    -1.0,
                    f"{bet.player} is on {team.team}'s report: {hit.status or 'out'}",
                    veto=True,
                )
        return None


# Prop market → the opposing defensive unit the stat runs through.
_PASS_MARKERS = ("pass", "receiv", "recept", "catch")
_RUSH_MARKERS = ("rush", "carr")


def _prop_unit(market: str) -> str | None:
    lowered = market.lower()
    if any(m in lowered for m in _PASS_MARKERS):
        return "pass_def"
    if any(m in lowered for m in _RUSH_MARKERS):
        return "rush_def"
    if "td" in lowered or "touchdown" in lowered:
        return "def_epa"
    return None


@dataclass(frozen=True)
class PropMatchupSignal:
    """Scores a prop against the defensive unit its stat runs through.

    Needs to know the player's team to find the opponent — ``player_teams``
    maps prop player names to the context's team keys (built from the
    FantasyPros projections snapshot). Abstains for unmapped players rather
    than guessing.
    """

    player_teams: Mapping[str, str] = field(default_factory=dict)
    name: str = "prop_matchup"

    def _team_for(self, player: str) -> str | None:
        if player in self.player_teams:
            return self.player_teams[player]
        key = _name_key(player)
        for name, team in self.player_teams.items():
            if _name_key(name) == key:
                return team
        return None

    def evaluate(self, bet: Bet, ctx: GameContext) -> SignalResult | None:
        if bet.player is None or bet.side not in _TOTAL_SIDES:
            return None
        unit = _prop_unit(bet.market)
        if unit is None:
            return None
        team = self._team_for(bet.player)
        if team == ctx.home.team:
            opp = ctx.away
        elif team == ctx.away.team:
            opp = ctx.home
        else:
            return None
        value = getattr(opp, unit)
        if value is None:
            return None
        z = ctx.zscore(unit, float(value))
        if z is None:
            return None
        # A leaky unit (high EPA allowed) confirms the over, contradicts the under.
        score = z * _total_sign(bet.side)
        softness = "soft" if z > 0 else "stingy"
        label = {"pass_def": "pass defense", "rush_def": "rush defense",
                 "def_epa": "defense"}[unit]
        return SignalResult(
            self.name,
            _clip(score),
            f"{opp.team} {label} is {softness} ({z:+.1f}σ, {value:+.3f} EPA/play allowed)",
        )


@dataclass(frozen=True)
class ExternalRatingSignal:
    """Agreement of an independent public rating system with the bet's side.

    "Use every available resource" made concrete: an external system that
    embeds information our results-only fit lacks (SP+ carries returning
    production, recruiting, and portal priors) either corroborates a play or
    argues with it. Per the intel contract it can never promote — a bet
    reaches this signal only after clearing the EV gate — and per the
    adverse-selection finding (docs/PUBLISH_GATE.md §2) an *uncorroborated*
    big edge is exactly the shape of a mispriced line, which is what makes
    disagreement here worth points of conviction.

    ``ratings`` maps team → (offense, defense) on the system's adjusted
    points-per-game scale (SP+ via :func:`velocity.ingest.ncaaf.sp_rating_table`
    — leak-gated to the latest finished season, so in-season it is last
    year's book: real but fading knowledge, priced accordingly by the modest
    default weight). Implied home margin adds ``hfa_points`` unless the
    market context says otherwise; ``scale`` is the points of agreement that
    saturate the score. Abstains when either team is unrated.
    """

    ratings: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    label: str = "SP+"
    hfa_points: float = 2.5
    scale: float = 7.0
    name: str = "external_rating"

    def _implied(self, ctx: GameContext) -> tuple[float, float] | None:
        home = self.ratings.get(ctx.home.team)
        away = self.ratings.get(ctx.away.team)
        if home is None or away is None:
            return None
        home_pts = (home[0] + away[1]) / 2.0  # home offense vs away defense
        away_pts = (away[0] + home[1]) / 2.0
        return home_pts - away_pts + self.hfa_points, home_pts + away_pts

    def evaluate(self, bet: Bet, ctx: GameContext) -> SignalResult | None:
        implied = self._implied(ctx)
        if implied is None:
            return None
        margin, total = implied
        point = None if bet.point is None else float(bet.point)
        if bet.market in ("spread", "moneyline") and bet.side in ("home", "away"):
            side_margin = margin if bet.side == "home" else -margin
            # A spread side covers when side_margin + point > 0 (slate.py's
            # convention); a moneyline just wants the side to win.
            agreement = side_margin if point is None else side_margin + point
            text = (f"{self.label} implies {ctx.home.team} by {margin:+.1f}"
                    if margin >= 0 else
                    f"{self.label} implies {ctx.away.team} by {-margin:+.1f}")
        elif bet.market == "total" and bet.side in _TOTAL_SIDES and point is not None:
            agreement = (total - point) * _total_sign(bet.side)
            text = f"{self.label} implies a total of {total:.1f} vs the {point:g} line"
        else:
            return None  # team totals / props: no external number to compare
        score = _clip(agreement / self.scale)
        stance = "corroborates" if score > 0 else "argues with"
        return SignalResult(
            self.name, score,
            f"{text} — {stance} the {bet.side} ({agreement:+.1f} pts)",
        )


def _is_na(value: object) -> bool:
    return value is None or value != value  # NaN is the only value unequal to itself


def _bp_prop_index(frame: object) -> dict[tuple[str, str], Mapping[str, object]]:
    """A banked BP props frame → {(normalized player, our market): row}.

    Rows without a mapped slug or a player name are dropped — an unmapped
    slug (or a pre-slug snapshot) abstains, never guesses.
    """
    from velocity.ingest.bettingpros import BP_PROP_SLUG_TO_MARKET  # noqa: PLC0415

    index: dict[tuple[str, str], Mapping[str, object]] = {}
    for row in frame.to_dict("records"):  # type: ignore[attr-defined]
        market = BP_PROP_SLUG_TO_MARKET.get(str(row.get("market_slug") or ""))
        player = row.get("player_name")
        if market is None or _is_na(player):
            continue
        index[(_name_key(str(player)), market)] = row
    return index


@dataclass(frozen=True)
class PropExternalSignal:
    """BettingPros' own projection agreeing or arguing with a prop play.

    The prop-side analogue of :class:`ExternalRatingSignal`: an independent
    outside system — here the banked BettingPros premium projection block
    (``recommended_side`` / ``probability`` / ``bet_rating``) — judges every
    prop that cleared the EV gate. Agreement scores positive, disagreement
    negative; magnitude comes from their own conviction (star rating out of
    five when present, else distance of their probability from a coin flip,
    else a modest default). Abstains for players or markets the snapshot
    doesn't project — including every row of a pre-slug snapshot — skipped,
    never guessed. Per the intel contract it can never promote a bet, and it
    never touches stakes.
    """

    index: Mapping[tuple[str, str], Mapping[str, object]] = field(default_factory=dict)
    label: str = "BettingPros"
    name: str = "prop_external"

    @classmethod
    def from_frame(cls, frame: object, label: str = "BettingPros") -> PropExternalSignal:
        """Build from a banked ``bp_props_*.parquet`` frame.

        Keys on (normalized player name, our market name via
        ``BP_PROP_SLUG_TO_MARKET``); rows without a mapped slug, a player
        name, or a recommended side contribute nothing.
        """
        index = {
            key: row for key, row in _bp_prop_index(frame).items()
            if not _is_na(row.get("recommended_side"))
        }
        return cls(index=index, label=label)

    def evaluate(self, bet: Bet, ctx: GameContext) -> SignalResult | None:
        if bet.player is None or bet.side not in _TOTAL_SIDES:
            return None
        row = self.index.get((_name_key(bet.player), bet.market))
        if row is None:
            return None
        side = str(row.get("recommended_side")).lower()
        if side not in _TOTAL_SIDES:
            return None
        rating, prob = row.get("bet_rating"), row.get("probability")
        if not _is_na(rating):
            magnitude = min(1.0, float(rating) / 5.0)  # type: ignore[arg-type]
            basis = f"rating {float(rating):g}/5"  # type: ignore[arg-type]
        elif not _is_na(prob):
            p = float(prob)  # type: ignore[arg-type]
            p = p / 100.0 if p > 1.0 else p  # the API has served both scales
            magnitude = min(1.0, max(0.3, 2.0 * abs(p - 0.5)))
            basis = f"p={p:.0%}"
        else:
            magnitude = 0.35  # a bare recommendation, taken at modest weight
            basis = "no rating"
        score = magnitude if side == bet.side else -magnitude
        proj = row.get("projection")
        proj_text = "" if _is_na(proj) else f", projects {float(proj):g}"  # type: ignore[arg-type]
        return SignalResult(
            self.name,
            _clip(score),
            f"{self.label} recommends the {side} ({basis}{proj_text})",
        )


# How far past consensus a prop number may sit before it reads as stale, per
# market — roughly the normal cross-book dispersion for each line type.
# Reasoned constants, stated as provisional: the banked snapshots will show
# the real dispersion once a few weeks accumulate.
DEFAULT_OUTLIER_POINTS: Mapping[str, float] = {
    "pass_yards": 15.0,
    "rush_yards": 10.0,
    "receiving_yards": 10.0,
    "receptions": 1.0,
    "pass_tds": 0.5,
}


@dataclass(frozen=True)
class PropLineOutlierSignal:
    """Demotes a prop whose shopped number is a favorable outlier vs consensus.

    The adverse-selection lesson (docs/PUBLISH_GATE.md §2) as a working rule:
    when our best-shopped line is far *friendlier* than the consensus number
    — a lower over, a higher under — the likeliest explanation is a stale
    line the market has moved off, not free money. This signal only ever
    demotes (score ≤ 0): sitting at or near consensus is the absence of a
    red flag, not corroboration, so it abstains rather than reward it.

    Demotion starts beyond the market's normal cross-book dispersion
    (``DEFAULT_OUTLIER_POINTS``) and saturates at twice it. Consensus lines
    are free-tier fields, so this works on any banked snapshot with slugs.
    """

    index: Mapping[tuple[str, str], Mapping[str, object]] = field(default_factory=dict)
    thresholds: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_OUTLIER_POINTS))
    label: str = "consensus"
    name: str = "line_outlier"

    @classmethod
    def from_frame(cls, frame: object) -> PropLineOutlierSignal:
        return cls(index=_bp_prop_index(frame))

    def evaluate(self, bet: Bet, ctx: GameContext) -> SignalResult | None:
        if bet.player is None or bet.side not in _TOTAL_SIDES or bet.point is None:
            return None
        threshold = self.thresholds.get(bet.market)
        if threshold is None:
            return None
        row = self.index.get((_name_key(bet.player), bet.market))
        if row is None:
            return None
        consensus = row.get(f"consensus_{bet.side}_line")
        if _is_na(consensus):  # the paired side's consensus is the same number
            consensus = row.get(
                f"consensus_{'under' if bet.side == 'over' else 'over'}_line")
        if _is_na(consensus):
            return None
        # Favorable deviation: an over bet on a LOWER number than consensus,
        # an under on a HIGHER one.
        favorable = (float(consensus) - float(bet.point)) * _total_sign(bet.side)  # type: ignore[arg-type]
        if favorable <= threshold:
            return None  # inside normal book dispersion — nothing to flag
        score = -min(1.0, (favorable - threshold) / threshold)
        return SignalResult(
            self.name, score,
            f"our {bet.point:g} sits {favorable:g} pts friendlier than the "
            f"{float(consensus):g} {self.label} line — stale-line shape",  # type: ignore[arg-type]
        )


def default_game_signals() -> tuple[MatchupSignal, FormSignal, RestSignal, InjurySignal]:
    """The standing signal set for game markets."""
    return (MatchupSignal(), FormSignal(), RestSignal(), InjurySignal())


def default_prop_signals(
    player_teams: Mapping[str, str] | None = None,
) -> tuple[PropAvailabilitySignal, PropMatchupSignal]:
    """The standing signal set for player props.

    The game injury signal deliberately stays out: teammates' outs cut both
    ways for a prop (a thinner offense scores less, but survivors absorb the
    vacated usage — ``redistribute_shares``), so scoring that honestly needs
    player-level share data, not a report headcount.
    """
    return (
        PropAvailabilitySignal(),
        PropMatchupSignal(player_teams=dict(player_teams or {})),
    )
