"""Curated plays — the staked board ranked into A+ / A / B / Watch, with reasons.

The repo already tiers a play twice, on different evidence and for different
readers:

* **Rule tiers** (:mod:`velocity.wagering.tiers`) — A/B from the wager lab's
  walk-forward record of the rule that admitted the play. Evidence about the
  *rule*, measured over a decade of seasons.
* **Intel tiers** (:mod:`velocity.intel`) — A/B/C/flagged from the game's
  context: matchups, form, rest, the injury report. Evidence about *this*
  game, which can veto but never promote.

Neither is the curated list. A rule tier says "unders 4+ have gone 56.2% over
299 bets"; it says nothing about whether the sim likes *this* under by four
points or by twelve. An intel tier says the context agrees; it says nothing
about whether the rule behind the play has ever won.

This module composes them, plus the edge and the stake, into one public
ranking a human acts on, and — the part that matters — makes every play state
its own case. A tier with no argument attached is a tip, and a tip is what
this system exists not to produce.

Nothing here re-prices anything. Edge, probability and stake are read off the
rows the slate already staked; the composition is ranking and prose.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from velocity.wagering.tiers import RuleTier

# The public tiers, best first.
TIER_A_PLUS = "A+"
TIER_A = "A"
TIER_B = "B"
TIER_WATCH = "Watch"
TIERS: tuple[str, ...] = (TIER_A_PLUS, TIER_A, TIER_B, TIER_WATCH)
_TIER_ORDER = {tier: i for i, tier in enumerate(TIERS)}

PLAY_COLUMNS: tuple[str, ...] = (
    "tier",
    "bet_type",
    "selection",
    "market",
    "edge",
    "confidence",
    "stake",
    "reason",
    "matchup",
    "kickoff",
    "game_id",
    "league",
    "side",
    "point",
    "book",
    "price",
    "p_model",
    "p_fair",
    "kelly_fraction",
    "rule_tier",
    "intel_tier",
)


@dataclass(frozen=True)
class PlaysConfig:
    """Where the tier lines sit, and how confidence is blended.

    Every number here is a policy choice, not a measurement, which is exactly
    why it lives in one frozen object the tests pin rather than scattered
    through the code as literals. Changing a line changes what gets published;
    it does not change what the model thinks.
    """

    # Confidence blend. Components absent on a row are dropped and the
    # remaining weights renormalized, so a play with no intel read is scored
    # on its edge and its rule record rather than penalized for the silence.
    edge_weight: float = 0.4
    rule_weight: float = 0.35
    intel_weight: float = 0.25

    # The edge that scores a full ten on the edge component. Anything past it
    # is capped: a 40% "edge" is a data error, not four times the conviction.
    edge_ceiling: float = 0.10

    # A rule's win rate maps onto [0, 1] across this band. 50% is a coin flip
    # and scores zero; 60% on a walk-forward record is about as good as this
    # system's promoted rules get and scores one.
    rule_floor: float = 0.50
    rule_ceiling: float = 0.60

    # Tier lines, applied in order (A+ first).
    a_plus_confidence: float = 7.0
    a_confidence: float = 5.5
    # A+ additionally requires a rule with a walk-forward record behind it.
    a_plus_requires_rule: bool = True
    # Below this edge a staked row is still listed, but only to watch.
    watch_edge: float = 0.0


def _finite(value: object) -> float | None:
    """A cell as a float, or ``None`` when it is missing or not a number."""
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return None
    number = float(number)
    return number if np.isfinite(number) else None


def edge_score(edge: float | None, config: PlaysConfig) -> float | None:
    """The edge component of confidence, on [0, 1]. Negative edge scores zero."""
    if edge is None:
        return None
    return float(np.clip(edge / config.edge_ceiling, 0.0, 1.0))


def rule_score(rule: RuleTier | None, config: PlaysConfig) -> float | None:
    """The rule-record component, on [0, 1], or ``None`` with no rule.

    The win rate is the headline, but a rule that cleared eleven of fifteen
    seasons and one that cleared six are not the same claim at the same win
    rate, so the seasons cleared scale it.
    """
    if rule is None:
        return None
    span = max(config.rule_ceiling - config.rule_floor, 1e-9)
    rate = float(np.clip((rule.win_rate - config.rule_floor) / span, 0.0, 1.0))
    seasons = float(rule.seasons_cleared) / float(rule.seasons) if rule.seasons else 0.0
    return float(np.clip(rate, 0.0, 1.0) * np.clip(seasons, 0.0, 1.0))


def confidence_score(
    edge: float | None,
    rule: RuleTier | None,
    conviction: float | None,
    config: PlaysConfig | None = None,
) -> float:
    """The blended 0-10 confidence the card prints.

    Zero when nothing can be scored — which is honest: a row with no edge, no
    rule record and no context read has no case, and printing a middling
    number for it would invent one.
    """
    config = config or PlaysConfig()
    parts = (
        (edge_score(edge, config), config.edge_weight),
        (rule_score(rule, config), config.rule_weight),
        (None if conviction is None else float(np.clip(conviction, 0.0, 1.0)),
         config.intel_weight),
    )
    live = [(value, weight) for value, weight in parts if value is not None and weight > 0]
    if not live:
        return 0.0
    total = sum(weight for _, weight in live)
    return round(10.0 * sum(value * weight for value, weight in live) / total, 1)


def assign_tier(
    *,
    confidence: float,
    stake: float,
    edge: float | None,
    rule: RuleTier | None,
    vetoed: bool,
    config: PlaysConfig | None = None,
) -> str:
    """The public tier for one play.

    ``Watch`` is not a failure state, it is the honest one: a vetoed play, a
    paper row, a zero stake or a non-positive edge all mean "the system is
    looking at this and is not betting it", and saying so is more use than
    dropping the row and leaving the reader to wonder where it went.
    """
    config = config or PlaysConfig()
    if vetoed or stake <= 0.0 or edge is None or edge <= config.watch_edge:
        return TIER_WATCH
    if confidence >= config.a_plus_confidence and (rule is not None
                                                   or not config.a_plus_requires_rule):
        return TIER_A_PLUS
    if confidence >= config.a_confidence:
        return TIER_A
    return TIER_B


def tier_rank(tier: str) -> int:
    """Sort key: A+ before A before B before Watch."""
    return _TIER_ORDER.get(str(tier), len(TIERS))


# --------------------------------------------------------------------------
# Selection and reason text
# --------------------------------------------------------------------------

def matchup_text(*, home_team: str, away_team: str) -> str | None:
    """``away @ home``, or None when the card does not know the game.

    None rather than a half-filled string: "@ Falcons" reads like a road game
    against nobody, and a blank cell is the honest way to say the game_id did
    not join. Away first because that is how every board prints a matchup.
    """
    home, away = (home_team or "").strip(), (away_team or "").strip()
    if not home or not away:
        return None
    return f"{away} @ {home}"


def selection_text(
    market: str,
    side: str,
    point: float | None,
    *,
    home_team: str = "",
    away_team: str = "",
    player: str | None = None,
) -> str:
    """The play as a human says it: ``"Atlanta +2.5"``, ``"Under 44.5"``.

    Spread and moneyline sides are canonicalized to ``home``/``away`` by the
    time a bet is logged, so the team name has to come back from the game's
    own row — a card that reads "home +2.5" is a card nobody can act on.
    """
    market = str(market)
    side = str(side).lower()
    if player:
        line = "" if point is None else f" {point:g}"
        return f"{player} {side.title()}{line} {market}".strip()
    if market in ("total", "team_total_home", "team_total_away"):
        team = home_team if market.endswith("_home") else away_team
        label = f"{team} team total " if market != "total" else ""
        line = "" if point is None else f" {point:g}"
        return f"{label}{side.title()}{line}".strip()
    team = home_team if side == "home" else away_team if side == "away" else side
    if market == "moneyline":
        return f"{team} ML".strip()
    if point is None:
        return f"{team} {side}".strip()
    return f"{team} {point:+g}".strip()


def reason_text(  # noqa: PLR0913 - the card's argument takes the card's parts
    *,
    selection: str,
    market: str,
    model_line: float | None = None,
    disagreement: float | None = None,
    probability: float | None = None,
    fair_probability: float | None = None,
    kelly_fraction: float | None = None,
    confidence: float | None = None,
    rule: RuleTier | None = None,
    intel_rationale: str | None = None,
    veto: bool = False,
) -> str:
    """One line of argument for one play.

    Single-line on purpose. A CSV cell can hold a newline and Power Query will
    carry it, but Excel renders a multi-line cell as one tall row that pushes
    the rest of the table off the screen, and this column is meant to be read
    beside the numbers rather than instead of them.
    :func:`explain` gives the same argument in the block form the console and
    the cards use.
    """
    parts: list[str] = []
    if model_line is not None:
        label = "Model total" if market.startswith("total") or market.endswith("total") \
            else "Model line"
        parts.append(f"{label} {model_line:g}")
    if disagreement is not None:
        unit = "points" if abs(disagreement) != 1 else "point"
        parts.append(f"{disagreement:+.1f} {unit} of disagreement")
    if probability is not None:
        parts.append(f"model {probability:.1%}")
    if fair_probability is not None:
        parts.append(f"fair {fair_probability:.1%}")
    if kelly_fraction is not None:
        parts.append(f"Kelly {kelly_fraction:.2%}")
    if rule is not None:
        parts.append(f"rule {rule.tier} ({rule.name}): {rule.record}")
    if confidence is not None:
        parts.append(f"confidence {confidence:.1f}")
    if intel_rationale:
        parts.append(str(intel_rationale))
    if veto:
        parts.insert(0, "VETOED by the intel layer — not recommended")
    return f"{selection} — " + " · ".join(parts) if parts else selection


def explain(row: Mapping[str, object]) -> str:
    """The same argument as a block, for a console card or a PNG.

    Mirrors the shape the migration brief asked for: the call, the model's
    number, the disagreement, the probability, the stake and the confidence,
    one per line.
    """
    def cell(key: str) -> object:
        return row.get(key)

    lines = [str(cell("selection") or "")]
    reason = str(cell("reason") or "")
    body = reason.split(" — ", 1)[1] if " — " in reason else reason
    lines.extend(part.strip() for part in body.split(" · ") if part.strip())
    return "\n".join(line for line in lines if line)


# --------------------------------------------------------------------------
# The frame builder
# --------------------------------------------------------------------------

def _model_line_and_disagreement(
    market: str, side: str, point: float | None,
    fair_spread: float | None, fair_total: float | None,
) -> tuple[float | None, float | None]:
    """The model's number for this side, and how far it sits from the market.

    Positive disagreement always means *the model is on this side of the
    number*, whichever side that is — the same sign convention
    :func:`velocity.wagering.slate.total_disagreement` uses, extended to the
    spread by normalizing to the home number first and flipping back.
    """
    side = str(side).lower()
    if market == "total":
        if fair_total is None or point is None:
            return fair_total, None
        return fair_total, (fair_total - point) if side == "over" else (point - fair_total)
    if market == "spread":
        if fair_spread is None:
            return None, None
        model_line = fair_spread if side == "home" else -fair_spread
        if point is None:
            return model_line, None
        home_point = point if side == "home" else -point
        home_value = home_point - fair_spread
        return model_line, home_value if side == "home" else -home_value
    return None, None


def _intel_index(intel: pd.DataFrame | None) -> dict[tuple, dict[str, object]]:
    """``(game_id, player, market, side) → {conviction, tier, recommended, rationale}``."""
    if intel is None or intel.empty:
        return {}
    out: dict[tuple, dict[str, object]] = {}
    for row in intel.to_dict("records"):
        player = row.get("player")
        key = (
            str(row.get("game_id")),
            "" if player is None or pd.isna(player) else str(player),
            str(row.get("market")),
            str(row.get("side")).lower(),
        )
        out[key] = {
            "conviction": row.get("conviction"),
            "tier": row.get("tier"),
            "recommended": row.get("recommended", True),
            "rationale": row.get("rationale"),
        }
    return out


def build_plays(  # noqa: PLR0913, PLR0915 - the curated card takes the run's parts
    slate: pd.DataFrame | None = None,
    *,
    props: pd.DataFrame | None = None,
    games: pd.DataFrame | None = None,
    projections: pd.DataFrame | None = None,
    intel: pd.DataFrame | None = None,
    bankroll: float = 100.0,
    config: PlaysConfig | None = None,
) -> pd.DataFrame:
    """Every staked or papered row, ranked A+ / A / B / Watch, each with its case.

    Rows come from the slate frames as banked. Nothing is re-priced and
    nothing is dropped for being unattractive: a vetoed or zero-staked row
    becomes a ``Watch``, because the reader is better served by "seen, not
    bet" than by a silent absence.
    """
    config = config or PlaysConfig()
    teams: dict[str, tuple[str, str]] = {}
    leagues: dict[str, str] = {}
    kicks: dict[str, pd.Timestamp] = {}
    if games is not None and not games.empty and "game_id" in games.columns:
        has_kickoff = "kickoff" in games.columns
        for row in games.drop_duplicates(subset=["game_id"]).to_dict("records"):
            gid = str(row.get("game_id"))
            teams[gid] = (str(row.get("home_team") or ""), str(row.get("away_team") or ""))
            if row.get("league") is not None:
                leagues[gid] = str(row.get("league"))
            # A prop names a player, never the game. Without the kickoff the
            # reader cannot tell a card that is still bettable from one whose
            # game has started, and without the matchup cannot tell who the
            # player is even playing. Both are already on this frame.
            if has_kickoff:
                when = pd.to_datetime(str(row.get("kickoff")), errors="coerce", utc=True)
                if not pd.isna(when):
                    kicks[gid] = pd.Timestamp(when)

    fair: dict[str, tuple[float | None, float | None]] = {}
    if projections is not None and not projections.empty and "game_id" in projections.columns:
        for row in projections.drop_duplicates(subset=["game_id"]).to_dict("records"):
            fair[str(row.get("game_id"))] = (
                _finite(row.get("fair_spread")), _finite(row.get("fair_total"))
            )

    verdicts = _intel_index(intel)
    sources = [(slate, "game"), (props, "prop")]
    rows: list[dict[str, object]] = []

    for frame, bet_type in sources:
        if frame is None or frame.empty:
            continue
        for record in frame.to_dict("records"):
            gid = str(record.get("game_id"))
            market = str(record.get("market"))
            side = str(record.get("side")).lower()
            raw_player = record.get("player")
            player = (None if raw_player is None or pd.isna(raw_player)
                      else str(raw_player))
            point = _finite(record.get("point"))
            edge = _finite(record.get("edge"))
            stake = _finite(record.get("stake")) or 0.0
            league = str(record.get("league") or leagues.get(gid, ""))
            home_team, away_team = teams.get(gid, ("", ""))
            fair_spread, fair_total = fair.get(gid, (None, None))

            rule = None
            rule_tier = record.get("rule_tier")
            if rule_tier is not None and not pd.isna(rule_tier) and league:
                from velocity.wagering.tiers import rule_named

                rule = rule_named(league, market, str(rule_tier))

            verdict = verdicts.get((gid, player or "", market, side), {})
            conviction = _finite(verdict.get("conviction"))
            recommended = verdict.get("recommended", True)
            vetoed = bool(recommended is False or recommended is np.False_)

            confidence = confidence_score(edge, rule, conviction, config)
            tier = assign_tier(
                confidence=confidence, stake=stake, edge=edge,
                rule=rule, vetoed=vetoed, config=config,
            )
            # A paper row says so in its note; it is never a recommendation.
            note = record.get("note")
            papered = note is not None and not pd.isna(note)
            if papered:
                tier = TIER_WATCH

            model_line, disagreement = _model_line_and_disagreement(
                market, side, point, fair_spread, fair_total
            )
            selection = selection_text(
                market, side, point,
                home_team=home_team, away_team=away_team, player=player,
            )
            kelly = (stake / bankroll) if bankroll > 0 else None
            reason = reason_text(
                selection=selection,
                market=market,
                model_line=model_line,
                disagreement=disagreement,
                probability=_finite(record.get("p_model")),
                fair_probability=_finite(record.get("p_fair")),
                kelly_fraction=kelly,
                confidence=confidence,
                rule=rule,
                intel_rationale=(None if verdict.get("rationale") is None
                                 else str(verdict.get("rationale"))),
                veto=vetoed,
            )
            if papered and not vetoed:
                reason = f"{reason} · paper: {note}"

            rows.append({
                "tier": tier,
                "bet_type": bet_type,
                "selection": selection,
                "market": market,
                "edge": edge,
                "confidence": confidence,
                "stake": round(stake, 2),
                "reason": reason,
                "matchup": matchup_text(home_team=home_team, away_team=away_team),
                "kickoff": kicks.get(gid),
                "game_id": gid,
                "league": league,
                "side": side,
                "point": point,
                "book": record.get("book"),
                "price": _finite(record.get("price")),
                "p_model": _finite(record.get("p_model")),
                "p_fair": _finite(record.get("p_fair")),
                "kelly_fraction": None if kelly is None else round(kelly, 4),
                "rule_tier": None if rule is None else rule.tier,
                "intel_tier": verdict.get("tier"),
            })

    out = pd.DataFrame(rows, columns=list(PLAY_COLUMNS))
    if out.empty:
        return out
    out["_rank"] = out["tier"].map(tier_rank)
    out = out.sort_values(
        ["_rank", "confidence", "edge"], ascending=[True, False, False]
    ).drop(columns="_rank")
    return out.reset_index(drop=True)
