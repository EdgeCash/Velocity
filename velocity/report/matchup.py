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

from velocity.report.social import SPREAD_EDGE_PTS, TOTAL_EDGE_PTS, SocialCard

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
    # There is deliberately no confidence score here. Measured on 15,731
    # leak-safe walk-forward projections (3,904 NFL + 11,827 NCAAF), nothing
    # the model knows about itself predicts how far its projection lands from
    # the final: every candidate correlates |r| < 0.04 against absolute margin
    # and total error, and the sim's own dispersion runs the WRONG way in the
    # NFL (highest-dispersion quintile misses by 0.89 points LESS).
    #
    # The cause is structural rather than a calibration that needs work. The
    # sim's sds are near-constant by construction -- sd_margin varies by a
    # coefficient of 0.018 (NFL) / 0.024 (NCAAF) across sixteen seasons -- so
    # the model does not produce a per-game uncertainty estimate at all, and
    # there is nothing to calibrate. A 0-10 number built on it would sit near
    # one value forever while reading as meaning.
    #
    # The distributions already carry this honestly: the curve IS the
    # uncertainty, and its width is visible without a score implying a
    # precision the model does not have.

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


# --- building a card from the slate's own objects ---------------------------

# Card rank key → (epa_form column, lower_is_better). Direction is the whole
# point of this table: ``off_epa`` is EPA gained, so 1st is the HIGHEST, while
# every defensive column is EPA *allowed*, so 1st is the LOWEST. Rank a
# defense the offensive way and the card prints the league's leakiest unit as
# its best — a mistake nothing downstream would catch, because a rank is a
# plausible small integer either way.
RANK_UNITS: tuple[tuple[str, str, bool], ...] = (
    ("off", "off_epa", False),
    ("def", "def_epa", True),
    ("off_pass", "pass_off", False),
    ("off_rush", "rush_off", False),
    ("def_pass", "pass_def", True),
    ("def_rush", "rush_def", True),
)

# The card's fixed player shape: a quarterback, a back, and two pass-catchers,
# each with the two markets that describe his night. Positions are the
# projection provider's own labels.
PLAYER_SLOTS: tuple[tuple[str, tuple[str, ...], str, str, str, str], ...] = (
    ("QB", ("QB",), "pass_yards", "PASS YDS", "pass_tds", "PASS TD"),
    ("RB", ("RB",), "rush_yards", "RUSH YDS", "anytime_td", "TOT TD"),
    ("", ("WR", "TE"), "receiving_yards", "REC YDS", "receptions", "REC"),
)
# Markets printed as whole numbers; everything else gets a decimal, because
# "1.8 passing touchdowns" is a projection and "2" looks like a claim.
_WHOLE = frozenset({"pass_yards", "rush_yards", "receiving_yards", "rush_rec_yards"})

# A unit mismatch has to be worth a sentence. Eight places on a 32-team board
# is a quarter of the league between the two units; below that the ranks are
# inside their own noise and the note would be filler.
NOTE_GAP_MIN = 8

# How much of an unmapped opponent's name a form chip can carry. Three chips
# share one half of the card, so this is a layout limit, not a taste: at 7 the
# longest label still clears the next chip's box at the rendered size. The pro
# leagues never reach it (codes are 2-3 characters) and every FBS school maps
# to its abbreviation, so this only ever trims an FCS visitor.
OPPONENT_CHARS = 7
_NOTE_PAIRS = (("off_pass", "def_pass", "passing game", "pass defense"),
               ("off_rush", "def_rush", "running game", "run defense"))


def split_name(full_name: str, code: str, mascot: str | None = None) -> tuple[str, str]:
    """``"Dallas Cowboys"`` → ``("Dallas", "Cowboys")``.

    ``mascot``, when the identity source states it, is authoritative and the
    place is whatever the name has left once it is removed.

    Without one the nickname is the last word and the place is everything
    before it. That is right for all 32 NFL clubs, whose nicknames are every
    one a single word, and WRONG for a great many college teams: Crimson Tide,
    Fighting Irish, Blue Devils, Tar Heels, Yellow Jackets and Horned Frogs all
    split into a stray adjective and a fragment ("Alabama Crimson" / "Tide").

    So the fallback is a fallback, not a rule that happens to hold. College
    identity comes from CFBD, which states school and mascot in separate
    fields; pass the mascot and none of this guessing applies. A name with no
    space at all falls back to the club code as the place.
    """
    name = str(full_name)
    if mascot:
        head = name[: -len(mascot)].strip() if name.endswith(mascot) else name
        return (head or code), mascot
    parts = name.split()
    if len(parts) < 2:
        return code, name
    return " ".join(parts[:-1]), parts[-1]


def unit_ranks(epa: pd.DataFrame | None) -> dict[str, dict[str, UnitRank]]:
    """Team → card rank key → :class:`UnitRank`, over one ``epa_form`` frame.

    Ties take the better (lower) rank, and the field size is the number of
    teams the frame actually covers, so a partial week reads "of 27" rather
    than claiming a full league.
    """
    if epa is None or epa.empty:
        return {}
    out: dict[str, dict[str, UnitRank]] = {}
    for key, column, lower_is_better in RANK_UNITS:
        if column not in epa.columns:
            continue
        values = pd.to_numeric(epa[column], errors="coerce").dropna()
        if values.empty:
            continue
        ranks = values.rank(ascending=lower_is_better, method="min")
        of = int(len(values))
        for (team, rank), value in zip(ranks.items(), values, strict=True):
            out.setdefault(str(team), {})[key] = UnitRank(
                rank=int(rank), of=of, value=float(value))
    return out


def form_games(games: pd.DataFrame | None, team: str, season: int, n: int = 3,
               codes: Mapping[str, str] | None = None) -> tuple[FormGame, ...]:
    """The team's last ``n`` completed games in ``season``, oldest first.

    Oldest-first matches :func:`velocity.report.deepdive.team_form` and reads
    left-to-right toward today, which is how the chips are laid out.
    """
    if games is None or games.empty:
        return ()
    played = games[
        (games["season"] == season)
        & games["home_score"].notna() & games["away_score"].notna()
        & ((games["home_team"] == team) | (games["away_team"] == team))
    ]
    if played.empty:
        return ()
    order = "kickoff" if "kickoff" in played.columns else ["season", "week"]
    played = played.sort_values(order).tail(n)
    short = dict(codes or {})
    out: list[FormGame] = []
    for g in played.to_dict("records"):
        at_home = g["home_team"] == team
        us = int(g["home_score"] if at_home else g["away_score"])
        them = int(g["away_score"] if at_home else g["home_score"])
        other = str(g["away_team"] if at_home else g["home_team"])
        out.append(FormGame(
            result="W" if us > them else ("L" if us < them else "T"),
            points_for=us, points_against=them,
            opponent=short.get(other, other)[:OPPONENT_CHARS], at_home=at_home))
    return tuple(out)


def roster_from_projections(fp: pd.DataFrame) -> pd.DataFrame:
    """The projection frame reduced to identity: key, name, position, team.

    :class:`~velocity.models.props_football.FootballPropSim` is keyed by player
    key alone and carries no position, so the card cannot fill a QB/RB/WR shape
    from the sim by itself. This is the smallest thing that closes that gap.
    """
    from velocity.models.props_football import player_key

    rows = fp.drop_duplicates(subset=["player_name"]).to_dict("records")
    return pd.DataFrame([{
        "player_key": player_key(r.get("player_id"), r.get("player_name")),
        "player_name": str(r.get("player_name")),
        "position": str(r.get("position") or ""),
        "team": str(r.get("team") or ""),
    } for r in rows])


def _sim_mean(sim: object, key: str, market: str) -> float | None:
    """The sim's mean for one market, or None when it does not price it.

    A market under its floor is simply absent from the sim, and the card says
    nothing rather than reaching past the model for the provider's raw number.
    """
    if not getattr(sim, "has", lambda *_: False)(key, market):
        return None
    return float(sim.mean(key, market))  # type: ignore[attr-defined]


def _format(value: float, market: str) -> str:
    return f"{value:.0f}" if market in _WHOLE else f"{value:.1f}"


def player_lines(sim: object | None, roster: pd.DataFrame | None,
                 team: str) -> tuple[PlayerLine, ...]:
    """One team's four projected rows: QB, RB, and the top two pass-catchers.

    Each slot goes to whoever the model projects for the most of that slot's
    primary market, which is what a depth chart is by the time it reaches a
    projection. A slot with nobody priced is simply left out.
    """
    if sim is None or roster is None or roster.empty:
        return ()
    mine = roster[roster["team"].astype(str) == str(team)]
    if mine.empty:
        return ()
    rows: list[PlayerLine] = []
    taken: set[str] = set()
    for label, positions, market, stat, market2, stat2 in PLAYER_SLOTS:
        # The catcher slot runs twice; every other slot once.
        wanted = 2 if not label else 1
        pool = [
            (value, r) for r in mine.to_dict("records")
            if str(r["position"]) in positions
            and str(r["player_key"]) not in taken
            and (value := _sim_mean(sim, str(r["player_key"]), market)) is not None
        ]
        pool.sort(key=lambda pair: pair[0], reverse=True)
        for value, r in pool[:wanted]:
            key = str(r["player_key"])
            taken.add(key)
            second = _sim_mean(sim, key, market2)
            rows.append(PlayerLine(
                position=label or str(r["position"]),
                name=str(r["player_name"]),
                stat=stat, value=_format(value, market),
                stat2=None if second is None else stat2,
                value2=None if second is None else _format(second, market2),
            ))
    return tuple(rows)


def unit_notes(away: TeamSide, home: TeamSide) -> tuple[str, ...]:
    """Up to three sentences on the widest unit mismatches, largest first.

    Fires in EITHER direction -- an offense that outranks the defense across
    from it, or a defense that outranks the offense -- and states the sentence
    from the winning unit's side. Ranking the gap by magnitude rather than by
    sign is what lets a smothering defense be the headline, which is often the
    true shape of a lopsided game.

    Derived from the ranks already on the card rather than written: the card
    should not assert anything the reader cannot check against the tracks a few
    inches above the sentence.
    """
    found: list[tuple[int, str]] = []
    for attack, defend in ((away, home), (home, away)):
        for off_key, def_key, phase, unit in _NOTE_PAIRS:
            off = attack.ranks.get(off_key)
            against = defend.ranks.get(def_key)
            if off is None or against is None:
                continue
            gap = against.rank - off.rank
            if abs(gap) < NOTE_GAP_MIN:
                continue
            # A mismatch is a mismatch whichever side of the ball wins it. The
            # rule used to fire only when the OFFENSE outranked the defense it
            # faced, which silently skipped the most one-sided games on the
            # card: a top-five defense against a bottom-third offense is the
            # story of that matchup, and the panel sat empty through it.
            if gap > 0:
                text = (f"{attack.code}'s {phase} ranks {_ordinal(off.rank)} and "
                        f"meets {defend.code}'s {unit}, ranked {_ordinal(against.rank)}.")
            else:
                text = (f"{defend.code}'s {unit} ranks {_ordinal(against.rank)} and "
                        f"meets {attack.code}'s {phase}, ranked {_ordinal(off.rank)}.")
            found.append((abs(gap), text))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return tuple(text for _, text in found[:3])


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:  # noqa: PLR2004 - the teens all take "th"
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def build_matchup_cards(  # noqa: PLR0913 - one graphic, assembled from the slate
    cards: Sequence[SocialCard],
    projections: Mapping[str, object],
    games: pd.DataFrame | None = None,
    plays: pd.DataFrame | None = None,
    *,
    week_label: str = "",
    league: str = "nfl",
    props_by_game: Mapping[str, object] | None = None,
    roster: pd.DataFrame | None = None,
    team_names: Mapping[str, str] | None = None,
    espn_ids: Mapping[str, int] | None = None,
    mascots: Mapping[str, str] | None = None,
    venue_by_game: Mapping[str, str] | None = None,
    notes_by_game: Mapping[str, Sequence[str]] | None = None,
    generated_at: pd.Timestamp | None = None,
) -> list[MatchupCard]:
    """One :class:`MatchupCard` per social card, from the slate's own objects.

    Deliberately built on top of :class:`~velocity.report.social.SocialCard`
    rather than beside it, exactly as :func:`velocity.report.deepdive.build_deep_dives`
    is: the card has already resolved display codes, kickoff, the projected
    means and the market view, and resolving them a second time is how two
    surfaces start disagreeing about which team is which.

    ``games``/``plays`` supply the records, the form chips and the EPA ranks;
    ``props_by_game`` + ``roster`` the player rows. Each is optional and its
    absence costs only its own section. ``team_names`` maps a card's display
    code back to the datasets' team key where they differ (NFL codes match;
    NCAAF cards carry abbreviations while the datasets key by school).
    """
    from velocity.report.deepdive import _record, epa_form, scoring_form

    season, scoring = (0, pd.DataFrame()) if games is None else scoring_form(games)
    ranks = unit_ranks(epa_form(plays, season) if plays is not None else None)
    names = dict(team_names or {})
    # The datasets' key → the card's display code, for the opponent on a chip.
    codes = {key: code for code, key in names.items()}
    stamp = pd.Timestamp.now(tz="UTC").tz_localize(None) if generated_at is None \
        else generated_at

    out: list[MatchupCard] = []
    for card in cards:
        proj = projections.get(card.game_id)
        if proj is None:
            continue
        sim = proj.sim  # type: ignore[attr-defined]
        margin = sim.margin.astype(int)
        total = sim.total.astype(int)
        sides = []
        for code, full_name in ((card.away_code, card.away_name),
                                (card.home_code, card.home_name)):
            key = names.get(code, code)
            city, nickname = split_name(full_name, code, (mascots or {}).get(code))
            sides.append(TeamSide(
                code=code, city=city, nickname=nickname,
                record=_record(scoring, key) if not scoring.empty else "",
                color=(card.away_color if code == card.away_code
                       else card.home_color) or "#8b96a3",
                espn_id=(espn_ids or {}).get(code),
                last3=form_games(games, key, season, codes=codes),
                ranks=ranks.get(key, {}),
                projections=player_lines(
                    (props_by_game or {}).get(card.game_id), roster, key),
            ))
        away, home = sides
        view = card.market_view
        built = MatchupCard(
            game_id=card.game_id, league=league, week_label=week_label,
            away=away, home=home,
            mu_away=card.mu_away, mu_home=card.mu_home,
            # SocialCard states the fair spread the way a book would (negative
            # for the favorite); this card's field is positive when the home
            # side is favored, so the sign flips here and nowhere else.
            fair_spread_home=-card.fair_spread,
            fair_total=card.fair_total,
            p_home_win=card.p_home_win,
            margin_pmf=_pmf_of(margin), total_pmf=_pmf_of(total),
            # Both spread fields flip here, and nowhere else. The board and
            # SocialCard state a spread the way a book does -- NEGATIVE for the
            # favorite -- while this card's two spread fields are positive when
            # the home side is favored so that model and market subtract
            # directly in ``spread_lean``. Mixing the conventions would not
            # crash: it would quietly print the other team's side.
            market=MarketNumbers(
                spread_home=(None if view is None or view.spread_home is None
                             else -view.spread_home),
                total=None if view is None else view.total),
            # Counted off the draws the curves are actually drawn from, not
            # copied from the card: the number under "MARGIN" is a claim about
            # this panel, and the two should not be able to drift apart.
            n_sims=int(len(margin)),
            venue=(venue_by_game or {}).get(card.game_id),
            kickoff=card.kickoff,
            generated_at=stamp,
            notes=tuple((notes_by_game or {}).get(card.game_id,
                                                  unit_notes(away, home))),
        )
        out.append(built)
    return out


def _pmf_of(values: object) -> dict[int, float]:
    """An integer sample array as a probability mass function."""
    import numpy as np

    arr = np.asarray(values)
    uniques, counts = np.unique(arr, return_counts=True)
    return {int(v): float(c) / len(arr) for v, c in zip(uniques, counts, strict=True)}
