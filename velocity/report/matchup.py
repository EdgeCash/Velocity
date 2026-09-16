"""Matchup card — the shareable per-game graphic, data side.

The account's front page: one card per game, built so a reader can form a
view **from the card alone** without it becoming a wall of numbers. Rendering
lives in :mod:`velocity.report.matchup_png`; everything here is pure.

It is deliberately a different object from :class:`~velocity.report.social.SocialCard`.
That card argues MARKET vs MODEL across three markets in one 16:9 frame. This
one answers a wider question — who are these teams, what does the model think,
and *why* — and needs the vertical room to do it.

What earns a place on it, and what does not:

* **The distributions are the card.** Telling a reader the model disagrees by
  5.5 points means little; showing where the market's number sits on 50,000
  simulated outcomes means a great deal, because the *width* is visible at the
  same time. A 5-point gap on a distribution 13 points wide reads as a lean,
  not a lock, without a word of hedging.
* **Ranks are EPA per play, not points per game.** Scoring ranks are an input
  the projection already absorbed, so printing them beside it is decoration.
  EPA/play is what :class:`~velocity.intel.signals.MatchupSignal` actually
  reads, so the ranks explain the projection instead of sitting next to it.
* **Player numbers are projections, never lines.** No prop board is quoted:
  a reader shops their own number, and a card that prints one implies a book.
* **Leans fire only past the published thresholds.** Everything else says "no
  edge", because a card where every market has a play is a tout sheet, and the
  blanks are what make the leans credible (the rule
  :mod:`velocity.report.social` already sets, kept here deliberately).
* **A generation stamp, always.** A model card without one invites the reader
  to wonder whether it was written after the line moved.

One thing the card deliberately does NOT do is shout where the disagreement is
widest. Split by stake (Kelly-sized, so monotone in edge), our own graded
record puts the *worst* closing-line value in the highest-edge bucket —
`corr(stake, CLV) = -0.35` (docs/PUBLISH_GATE.md §2). When the model screams
it is usually the market knowing something we do not, so a very wide gap earns
a caution note rather than a bigger headline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pandas as pd

from velocity.report.social import SPREAD_EDGE_PTS, TOTAL_EDGE_PTS

# Above this model-vs-market gap the number is a data-quality flag rather than
# a bigger edge — the adverse-selection finding, in points. Set at the spread
# bar's triple: past it, the honest card says "unusually wide" out loud.
WIDE_GAP_PTS = SPREAD_EDGE_PTS * 3.0


@dataclass(frozen=True)
class FormGame:
    """One recent result, as the form chips render it."""

    result: str  # "W" / "L" / "T"
    points_for: int
    points_against: int
    opponent: str
    at_home: bool

    def label(self) -> str:
        """``"vsPHI 24-21"`` / ``"@LV 33-16"`` — where it was and how it went."""
        where = "vs" if self.at_home else "@"
        return f"{where}{self.opponent} {self.points_for}-{self.points_against}"


@dataclass(frozen=True)
class UnitRank:
    """One team's standing in one unit measure."""

    rank: int
    of: int
    value: float

    def position(self) -> float:
        """Where the dot sits on a 0 (best) → 1 (worst) track."""
        if self.of <= 1:
            return 0.0
        return (self.rank - 1) / (self.of - 1)


@dataclass(frozen=True)
class PlayerLine:
    """One projected player row — our number, never a market line."""

    position: str
    name: str
    stat: str  # "PASS YDS"
    value: str  # "298"
    stat2: str | None = None
    value2: str | None = None


@dataclass(frozen=True)
class TeamSide:
    """Everything the card shows about one team."""

    code: str
    city: str
    nickname: str
    record: str
    color: str  # brand hex — identity marks only, never a data mark
    # ESPN's numeric team id, which is how the college logo CDN is addressed
    # (the pro leagues use a slug, keyed off ``code``). None = no mark, and
    # the renderer falls back to the code in brand color.
    espn_id: int | None = None
    last3: Sequence[FormGame] = field(default_factory=tuple)
    ranks: Mapping[str, UnitRank] = field(default_factory=dict)
    projections: Sequence[PlayerLine] = field(default_factory=tuple)


@dataclass(frozen=True)
class MarketNumbers:
    """The board, stated as fact. ``spread_home`` positive = home favored."""

    spread_home: float | None = None
    total: float | None = None


@dataclass(frozen=True)
class Lean:
    """One market's verdict: a label when the gap clears its published bar."""

    fired: bool
    label: str
    detail: str
    wide: bool = False


@dataclass(frozen=True)
class MatchupCard:
    """One game's graphic."""

    game_id: str
    league: str
    week_label: str
    away: TeamSide
    home: TeamSide
    mu_away: float
    mu_home: float
    fair_spread_home: float  # positive = home favored, matching the market
    fair_total: float
    p_home_win: float
    margin_pmf: Mapping[int, float] = field(default_factory=dict)
    total_pmf: Mapping[int, float] = field(default_factory=dict)
    market: MarketNumbers = field(default_factory=MarketNumbers)
    n_sims: int = 0
    venue: str | None = None
    kickoff: pd.Timestamp | None = None
    generated_at: pd.Timestamp | None = None
    notes: Sequence[str] = field(default_factory=tuple)
    # Per-market model confidence, 0-10. Provisional until the walk-forward
    # calibration lands, and the card says so rather than implying otherwise.
    confidence: Mapping[str, float] = field(default_factory=dict)

    def spread_lean(self) -> Lean:
        """The side call, stated at the market's own number."""
        market = self.market.spread_home
        if market is None:
            return Lean(False, "", "no market")
        diff = self.fair_spread_home - market
        wide = abs(diff) >= WIDE_GAP_PTS
        if abs(diff) < SPREAD_EDGE_PTS:
            return Lean(False, "", "no edge")
        # diff > 0: the model likes the home side MORE than the market does.
        # ``spread_home`` is positive when the home side is FAVORED, which is
        # a negative betting line for them — so the lean is stated at the side's
        # own number, not at the raw field. Getting this backwards prints
        # "DET +3.5" for a team laying 3.5, which is the wrong bet entirely.
        side = self.home.code if diff > 0 else self.away.code
        point = -market if diff > 0 else market
        return Lean(True, f"{side} {point:+g}",
                    f"{abs(diff):.1f} pts past the {SPREAD_EDGE_PTS:g} bar", wide)

    def total_lean(self) -> Lean:
        market = self.market.total
        if market is None:
            return Lean(False, "", "no market")
        diff = self.fair_total - market
        wide = abs(diff) >= WIDE_GAP_PTS
        if abs(diff) < TOTAL_EDGE_PTS:
            return Lean(False, "", "no edge")
        side = "OVER" if diff > 0 else "UNDER"
        return Lean(True, f"{side} {market:g}",
                    f"{abs(diff):.1f} pts past the {TOTAL_EDGE_PTS:g} bar", wide)

    def spread_label(self) -> str:
        """The model's line, team-anchored ("DET -8.0", or "PK")."""
        if abs(self.fair_spread_home) < 0.05:
            return "PK"
        if self.fair_spread_home > 0:
            return f"{self.home.code} {-self.fair_spread_home:+.1f}"
        return f"{self.away.code} {self.fair_spread_home:+.1f}"

    def market_spread_label(self) -> str:
        point = self.market.spread_home
        if point is None:
            return "—"
        if abs(point) < 0.05:
            return "PK"
        if point > 0:
            return f"{self.home.code} {-point:+g}"
        return f"{self.away.code} {point:+g}"

    def stamp(self) -> str:
        """``GENERATED 16 SEP 2026 · 22:44 UTC`` — the honesty line."""
        when = self.generated_at
        if when is None:
            return ""
        return f"GENERATED {when.strftime('%d %b %Y').upper()} · {when.strftime('%H:%M')} UTC"
