"""Matchup card — the leans' arithmetic, the labels' signs, and a render smoke.

The sign of a lean is the one thing on this card that can be wrong *publicly*:
"DET +3.5" and "DET -3.5" are opposite bets, and the card goes to an account,
not a log file. So every direction of every market gets pinned here rather than
sampled. Leans are keyed to the rule table (velocity.wagering.tiers): the
spread has no rule with a record, so its label arithmetic is pinned through a
test table that grants one, and the live card is pinned to say so. The render
is an offline smoke — no asset cache, so no network — that only asserts a real
PNG of the right frame lands.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image
from velocity.report.matchup import (
    WIDE_GAP_PTS,
    FormGame,
    MarketNumbers,
    MatchupCard,
    PlayerLine,
    TeamSide,
    UnitRank,
)
from velocity.report.matchup_png import HEIGHT, WIDTH, matchup_filename, render_matchup_card
from velocity.wagering import tiers as tiers_mod
from velocity.wagering.tiers import RULE_TIERS, RuleTier

# A test table that grants the spread a rule (2.5+ either side), so the
# label arithmetic below can be pinned in every direction. The live table
# has none: the lab measured no edge on spreads.
SPREAD_RULE = RuleTier("B", "spread", frozenset({"home", "away"}), 2.5, 0.53, 200, 6, 10)
WITH_SPREAD_RULE = {"nfl": (*RULE_TIERS["nfl"], SPREAD_RULE)}


@pytest.fixture
def spread_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tiers_mod, "RULE_TIERS", WITH_SPREAD_RULE)

AWAY = TeamSide(code="DAL", city="Dallas", nickname="Cowboys", record="6-5-1",
                color="#003594")
HOME = TeamSide(code="DET", city="Detroit", nickname="Lions", record="7-5",
                color="#0076B6")


def _card(*, fair_spread_home: float = 3.5, fair_total: float = 48.0,
          spread_home: float | None = 3.5, total: float | None = 48.0,
          **kw: object) -> MatchupCard:
    return MatchupCard(
        game_id="g1", league=str(kw.pop("league", "nfl")), week_label="Week 14",
        away=kw.pop("away", AWAY), home=kw.pop("home", HOME),  # type: ignore[arg-type]
        mu_away=22.0, mu_home=26.0,
        fair_spread_home=fair_spread_home, fair_total=fair_total,
        p_home_win=0.62,
        market=MarketNumbers(spread_home=spread_home, total=total),
        **kw,  # type: ignore[arg-type]
    )


# --- the spread lean, in all four directions -------------------------------
# ``spread_home``/``fair_spread_home`` are positive when the HOME side is
# favored, which is a *negative* betting line for that side. Each case names
# the bet a reader would place off the label.

@pytest.mark.parametrize(("fair", "market", "label"), [
    # model likes the home favorite more than the board → lay the home number
    (7.0, 3.5, "DET -3.5"),
    # model likes the home dog more than the board → take the home dog's points
    (-1.0, -4.0, "DET +4"),
    # model likes the away side more, home is favored → take the away points
    (0.5, 3.5, "DAL +3.5"),
    # model likes the away side more, away is favored → lay the away number
    (-7.0, -3.5, "DAL -3.5"),
])
@pytest.mark.usefixtures("spread_rule")
def test_spread_lean_states_the_side_at_its_own_number(
        fair: float, market: float, label: str) -> None:
    lean = _card(fair_spread_home=fair, spread_home=market).spread_lean()
    assert lean.fired
    assert lean.label == label
    assert lean.detail == "rule B · 53.0% on 200"


@pytest.mark.usefixtures("spread_rule")
def test_spread_lean_holds_inside_the_bar() -> None:
    lean = _card(fair_spread_home=3.5 + SPREAD_RULE.min_points - 0.1,
                 spread_home=3.5).spread_lean()
    assert not lean.fired
    assert lean.detail == "home by 2.4 · below the 2.5 bar"


@pytest.mark.usefixtures("spread_rule")
def test_spread_lean_fires_exactly_at_the_bar() -> None:
    assert _card(fair_spread_home=3.5 + SPREAD_RULE.min_points,
                 spread_home=3.5).spread_lean().fired


def test_spread_lean_on_the_live_table_says_no_rule_has_a_record() -> None:
    # The lab measured no edge on NFL spreads at any bar (and college spreads
    # inverted), so the live card shows the model's number and no lean — at
    # a gap that would have fired the old fixed 2.5-point bar three times over.
    lean = _card(fair_spread_home=11.0, spread_home=3.5).spread_lean()
    assert not lean.fired
    assert lean.detail == "no rule with a record"


def test_spread_lean_without_a_market_says_so() -> None:
    lean = _card(spread_home=None).spread_lean()
    assert not lean.fired
    assert lean.detail == "no market"


def test_a_very_wide_gap_is_flagged_rather_than_amplified() -> None:
    # Our own graded record puts the worst closing-line value in the
    # highest-edge bucket, so the card marks the outlier instead of leaning in
    # — on a market no rule covers. Where a rule admits the number the lab
    # measured that gap directly, so the caution stays off (the total below).
    lean = _card(fair_spread_home=3.5 + WIDE_GAP_PTS, spread_home=3.5).spread_lean()
    assert not lean.fired and lean.wide
    assert not _card(fair_total=48.0 - WIDE_GAP_PTS - 1.0, total=48.0).total_lean().wide


# --- the total lean --------------------------------------------------------

@pytest.mark.parametrize(("fair", "market", "label", "detail"), [
    (52.0, 48.0, "OVER 48", "rule B · 52.8% on 301"),
    (44.0, 48.0, "UNDER 48", "rule A · 55.6% on 340"),
])
def test_total_lean_names_the_side_at_the_posted_number(
        fair: float, market: float, label: str, detail: str) -> None:
    lean = _card(fair_total=fair, total=market).total_lean()
    assert lean.fired
    assert lean.label == label
    assert lean.detail == detail


def test_total_lean_holds_inside_the_bar() -> None:
    lean = _card(fair_total=48.0 + 3.9, total=48.0).total_lean()
    assert not lean.fired
    assert lean.detail == "over by 3.9 · below the 4 bar"


def test_college_total_lean_takes_unders_only() -> None:
    # The college table has unders at 4+ (B) and 8+ (A) and no over rule:
    # an 8-point under gap is tier A, and an over gap of any size says why
    # it is blank.
    under = _card(fair_total=40.0, total=48.0, league="ncaaf").total_lean()
    assert under.fired and under.label == "UNDER 48"
    assert under.detail == "rule A · 57.2% on 297"
    over = _card(fair_total=57.0, total=48.0, league="ncaaf").total_lean()
    assert not over.fired and over.detail == "no rule for overs"


# --- the printed lines -----------------------------------------------------

@pytest.mark.parametrize(("fair", "label"), [
    (8.0, "DET -8.0"),
    (-8.0, "DAL -8.0"),
    (0.0, "PK"),
])
def test_spread_label_is_team_anchored(fair: float, label: str) -> None:
    assert _card(fair_spread_home=fair).spread_label() == label


@pytest.mark.parametrize(("market", "label"), [
    (3.5, "DET -3.5"),
    (-3.5, "DAL -3.5"),
    (0.0, "PK"),
    (None, "—"),
])
def test_market_spread_label_is_team_anchored(market: float | None, label: str) -> None:
    assert _card(spread_home=market).market_spread_label() == label


def test_stamp_is_present_when_generated_at_is() -> None:
    card = _card(generated_at=pd.Timestamp("2026-09-16 22:44"))
    assert card.stamp() == "GENERATED 16 SEP 2026 · 22:44 UTC"
    assert _card().stamp() == ""


# --- the small value objects ----------------------------------------------

@pytest.mark.parametrize(("game", "label"), [
    (FormGame("W", 24, 21, "PHI", True), "vsPHI 24-21"),
    (FormGame("L", 16, 33, "LV", False), "@LV 16-33"),
])
def test_form_game_label(game: FormGame, label: str) -> None:
    assert game.label() == label


def test_unit_rank_position_spans_best_to_worst() -> None:
    assert UnitRank(1, 32, 0.0).position() == 0.0
    assert UnitRank(32, 32, 0.0).position() == 1.0
    assert UnitRank(1, 1, 0.0).position() == 0.0  # a one-team league never divides by 0


# --- render smokes ---------------------------------------------------------

def _pmf(mu: float, sd: float) -> dict[int, float]:
    rng = np.random.default_rng(7)
    draws = rng.normal(mu, sd, 20_000).round().astype(int)
    counts = np.bincount(draws - draws.min())
    return {int(draws.min() + i): float(c) / len(draws) for i, c in enumerate(counts)}


def _full_card() -> MatchupCard:
    form = (FormGame("W", 24, 21, "KC", True), FormGame("L", 17, 27, "PHI", False),
            FormGame("W", 31, 28, "NYG", True))
    ranks = {"off": UnitRank(9, 32, 0.07), "def": UnitRank(30, 32, 0.10),
             "off_pass": UnitRank(6, 32, 0.15), "def_rush": UnitRank(27, 32, 0.01)}
    players = (PlayerLine("QB", "Dak Prescott", "PASS YDS", "271", "PASS TD", "1.8"),
               PlayerLine("RB", "Javonte Williams", "RUSH YDS", "68", "TOT TD", "0.6"),
               PlayerLine("WR", "CeeDee Lamb", "REC YDS", "88", "REC", "6.9"),
               PlayerLine("WR", "George Pickens", "REC YDS", "71", "REC", "4.8"))
    side = {"last3": form, "ranks": ranks, "projections": players}
    return _card(
        away=TeamSide(code="DAL", city="Dallas", nickname="Cowboys", record="6-5-1",
                      color="#003594", **side),
        home=TeamSide(code="DET", city="Detroit", nickname="Lions", record="7-5",
                      color="#0076B6", **side),
        fair_spread_home=6.3, fair_total=51.0,
        margin_pmf=_pmf(6.3, 13.0), total_pmf=_pmf(51.0, 10.0),
        n_sims=20_000, venue="Ford Field",
        kickoff=pd.Timestamp("2025-12-07 13:00"),
        generated_at=pd.Timestamp("2026-09-16 22:44"),
        notes=("Detroit's rush defense meets a Dallas ground game that has "
               "cleared 100 yards twice in six weeks.",),
    )


def test_render_lands_a_png_of_the_right_frame(tmp_path: Path) -> None:
    out = render_matchup_card(_full_card(), tmp_path / "card.png")
    assert out.exists()
    assert Image.open(out).size == (WIDTH, HEIGHT)


def test_render_survives_a_card_with_nothing_optional(tmp_path: Path) -> None:
    # No pmf, no ranks, no players, no market, no logo cache: a card never
    # fails to render because a nicety was unavailable.
    out = render_matchup_card(
        _card(spread_home=None, total=None), tmp_path / "bare.png")
    assert Image.open(out).size == (WIDTH, HEIGHT)


def test_matchup_filename_names_the_game() -> None:
    assert matchup_filename(_card(), "20260101T000000Z") == (
        "matchup_nfl_DAL_DET_20260101T000000Z.png")


# --- the builder -----------------------------------------------------------
# Three things here are wrong-but-plausible if they invert, which is the whole
# reason they are pinned: a defensive rank ranked the offensive way, and either
# of the two spread fields carrying the board's sign instead of the card's.

def _games() -> pd.DataFrame:
    return pd.DataFrame([
        # oldest → newest, so the chips read left-to-right toward today
        {"season": 2025, "week": 11, "kickoff": pd.Timestamp("2025-11-16"),
         "home_team": "DAL", "away_team": "KC", "home_score": 24, "away_score": 21},
        {"season": 2025, "week": 12, "kickoff": pd.Timestamp("2025-11-23"),
         "home_team": "PHI", "away_team": "DAL", "home_score": 27, "away_score": 17},
        {"season": 2025, "week": 13, "kickoff": pd.Timestamp("2025-11-30"),
         "home_team": "DAL", "away_team": "NYG", "home_score": 28, "away_score": 28},
        {"season": 2025, "week": 13, "kickoff": pd.Timestamp("2025-11-30"),
         "home_team": "DET", "away_team": "GB", "home_score": 24, "away_score": 9},
    ])


def _plays() -> pd.DataFrame:
    # DAL: a good offense and a leaky defense. DET: the reverse of the defense.
    rows = []
    for posteam, defteam, play_type, epa in (
        ("DAL", "DET", "pass", 0.50), ("DAL", "DET", "run", 0.10),
        ("DET", "DAL", "pass", 0.20), ("DET", "DAL", "run", 0.05),
        ("GB", "NYG", "pass", -0.30), ("GB", "NYG", "run", -0.40),
        ("NYG", "GB", "pass", -0.10), ("NYG", "GB", "run", -0.20),
    ):
        rows.append({"season": 2025, "posteam": posteam, "defteam": defteam,
                     "play_type": play_type, "epa": epa})
    return pd.DataFrame(rows)


def test_unit_ranks_rank_defenses_by_fewest_points_allowed() -> None:
    from velocity.report.deepdive import epa_form
    from velocity.report.matchup import unit_ranks

    ranks = unit_ranks(epa_form(_plays(), 2025))
    # DAL gains the most EPA per play of the four, so 1st on offense; GB gains
    # the least, so last.
    assert ranks["DAL"]["off"].rank == 1
    assert ranks["GB"]["off"].rank == ranks["GB"]["off"].of
    # DET is the unit DAL's big plays ran through, so it ALLOWS the most and
    # ranks last on defense -- while NYG allows the least and ranks 1st. Ranked
    # the offensive way these two swap, and the card calls the league's leakiest
    # defense its best. Nothing downstream would catch that: a rank is a
    # plausible small integer either way.
    assert ranks["DET"]["def"].rank == ranks["DET"]["def"].of
    assert ranks["NYG"]["def"].rank == 1
    # ...and the same team is not ranked the same on both sides of the ball.
    assert ranks["DET"]["off"].rank == 2


def test_unit_ranks_field_size_is_the_teams_actually_covered() -> None:
    from velocity.report.deepdive import epa_form
    from velocity.report.matchup import unit_ranks

    ranks = unit_ranks(epa_form(_plays(), 2025))
    assert ranks["DAL"]["off"].of == 4  # not 32: a partial frame never claims a league


def test_unit_ranks_of_nothing_is_empty() -> None:
    from velocity.report.matchup import unit_ranks

    assert unit_ranks(None) == {}
    assert unit_ranks(pd.DataFrame()) == {}


def test_form_games_read_oldest_first_with_the_right_side() -> None:
    from velocity.report.matchup import form_games

    form = form_games(_games(), "DAL", 2025)
    assert [g.label() for g in form] == ["vsKC 24-21", "@PHI 17-27", "vsNYG 28-28"]
    assert [g.result for g in form] == ["W", "L", "T"]


def test_form_games_without_results_is_empty() -> None:
    from velocity.report.matchup import form_games

    assert form_games(None, "DAL", 2025) == ()
    assert form_games(_games(), "DAL", 2024) == ()


@pytest.mark.parametrize(("full", "city", "nickname"), [
    ("Dallas Cowboys", "Dallas", "Cowboys"),
    ("New York Giants", "New York", "Giants"),
    ("Ohio State Buckeyes", "Ohio State", "Buckeyes"),
    ("DAL", "DAL", "DAL"),  # a bare code falls back rather than splitting
])
def test_split_name_without_a_mascot_takes_the_last_word(
        full: str, city: str, nickname: str) -> None:
    from velocity.report.matchup import split_name

    assert split_name(full, "DAL") == (city, nickname)


@pytest.mark.parametrize(("full", "mascot", "city"), [
    ("Alabama Crimson Tide", "Crimson Tide", "Alabama"),
    ("Notre Dame Fighting Irish", "Fighting Irish", "Notre Dame"),
    ("Duke Blue Devils", "Blue Devils", "Duke"),
    ("North Carolina Tar Heels", "Tar Heels", "North Carolina"),
    ("Georgia Tech Yellow Jackets", "Yellow Jackets", "Georgia Tech"),
    ("TCU Horned Frogs", "Horned Frogs", "TCU"),
])
def test_split_name_uses_a_stated_mascot(full: str, mascot: str, city: str) -> None:
    """Two-word college nicknames are common, and the last-word rule mangles them.

    Without the mascot every one of these splits into a stray adjective and a
    fragment -- "Alabama Crimson" over "Tide" -- which is what the card printed
    before CFBD's own mascot field was carried through.
    """
    from velocity.report.matchup import split_name

    assert split_name(full, "ALA", mascot) == (city, mascot)
    # ...and the fallback really does get these wrong, which is why it is only
    # ever a fallback.
    assert split_name(full, "ALA") != (city, mascot)


def test_split_name_mascot_that_is_not_a_suffix_still_wins() -> None:
    # A display name that does not end in the mascot (a provider spelling
    # difference) keeps the name as the place rather than slicing it blindly.
    from velocity.report.matchup import split_name

    assert split_name("Miami (FL)", "MIA", "Hurricanes") == ("Miami (FL)", "Hurricanes")


def test_builder_takes_the_nickname_from_the_mascot_map() -> None:
    built = _build(card=_social(), mascots={"DET": "Lions", "DAL": "Cowboys"})[0]
    assert (built.home.city, built.home.nickname) == ("Detroit", "Lions")


@pytest.mark.parametrize(("n", "expected"), [
    (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"),
    (11, "11th"), (12, "12th"), (13, "13th"), (21, "21st"), (32, "32nd"),
])
def test_ordinal(n: int, expected: str) -> None:
    from velocity.report.matchup import _ordinal

    assert _ordinal(n) == expected


def _ranked(code: str, off_pass: int, def_pass: int) -> TeamSide:
    return TeamSide(code=code, city=code, nickname=code, record="", color="#fff",
                    ranks={"off_pass": UnitRank(off_pass, 32, 0.1),
                           "def_pass": UnitRank(def_pass, 32, 0.1)})


def test_unit_notes_only_speak_to_a_real_gap() -> None:
    from velocity.report.matchup import NOTE_GAP_MIN, unit_notes

    wide = unit_notes(_ranked("DAL", 2, 30), _ranked("DET", 20, 30))
    assert wide and "DAL" in wide[0] and "2nd" in wide[0] and "30th" in wide[0]

    narrow = unit_notes(_ranked("DAL", 10, 10 + NOTE_GAP_MIN - 1),
                        _ranked("DET", 10, 10 + NOTE_GAP_MIN - 1))
    assert narrow == ()


def test_unit_notes_let_a_dominant_defense_be_the_headline() -> None:
    """The one-directional version sat silent through the most lopsided games.

    LSU's 83rd pass offense against Georgia's 5th pass defense is the story of
    that matchup; firing only when an OFFENSE outranks its opponent skipped it
    and left the panel empty.
    """
    from velocity.report.matchup import unit_notes

    notes = unit_notes(_ranked("LSU", 83, 60), _ranked("UGA", 60, 5))
    assert notes, "a defense that outranks the offense it faces is a mismatch"
    # Stated from the winning unit's side: the defense leads the sentence.
    assert notes[0].startswith("UGA's pass defense ranks 5th")
    assert "LSU's passing game, ranked 83rd" in notes[0]


def test_unit_notes_rank_by_size_not_by_which_side_won_it() -> None:
    from velocity.report.matchup import unit_notes

    # Away offense beats the home defense by 10; home defense beats the away
    # offense by 25 on the other unit. The bigger gap leads, whichever it is.
    away = TeamSide(code="AAA", city="A", nickname="A", record="", color="#fff",
                    ranks={"off_pass": UnitRank(30, 32, 0.1),
                           "def_pass": UnitRank(20, 32, 0.1),
                           "off_rush": UnitRank(5, 32, 0.1),
                           "def_rush": UnitRank(16, 32, 0.1)})
    home = TeamSide(code="BBB", city="B", nickname="B", record="", color="#fff",
                    ranks={"off_pass": UnitRank(20, 32, 0.1),
                           "def_pass": UnitRank(5, 32, 0.1),
                           "off_rush": UnitRank(16, 32, 0.1),
                           "def_rush": UnitRank(15, 32, 0.1)})
    notes = unit_notes(away, home)
    assert notes[0].startswith("BBB's pass defense ranks 5th")  # gap 25
    assert any(n.startswith("AAA's running game ranks 5th") for n in notes)  # gap 10


def test_unit_notes_without_ranks_says_nothing() -> None:
    from velocity.report.matchup import unit_notes

    assert unit_notes(AWAY, HOME) == ()


class _Sim:
    """A stand-in FootballPropSim: fixed per-(player, market) means."""

    def __init__(self, means: dict[tuple[str, str], float]) -> None:
        self._means = means

    def has(self, player_id: str, market: str) -> bool:
        return (player_id, market) in self._means

    def mean(self, player_id: str, market: str) -> float:
        return self._means[(player_id, market)]


def _roster() -> pd.DataFrame:
    return pd.DataFrame([
        {"player_key": "qb1", "player_name": "Dak Prescott", "position": "QB",
         "team": "DAL"},
        {"player_key": "qb2", "player_name": "Backup Passer", "position": "QB",
         "team": "DAL"},
        {"player_key": "rb1", "player_name": "Javonte Williams", "position": "RB",
         "team": "DAL"},
        {"player_key": "wr1", "player_name": "CeeDee Lamb", "position": "WR",
         "team": "DAL"},
        {"player_key": "te1", "player_name": "Jake Ferguson", "position": "TE",
         "team": "DAL"},
        {"player_key": "wr2", "player_name": "Third Receiver", "position": "WR",
         "team": "DAL"},
        {"player_key": "other", "player_name": "Wrong Team", "position": "WR",
         "team": "DET"},
    ])


def _prop_sim() -> _Sim:
    return _Sim({
        ("qb1", "pass_yards"): 271.4, ("qb1", "pass_tds"): 1.84,
        ("qb2", "pass_yards"): 40.0,
        ("rb1", "rush_yards"): 68.2, ("rb1", "anytime_td"): 0.61,
        ("wr1", "receiving_yards"): 88.4, ("wr1", "receptions"): 6.9,
        ("te1", "receiving_yards"): 52.1, ("te1", "receptions"): 4.1,
        ("wr2", "receiving_yards"): 31.0, ("wr2", "receptions"): 2.4,
        ("other", "receiving_yards"): 999.0,
    })


def test_player_lines_fill_the_fixed_qb_rb_two_catcher_shape() -> None:
    from velocity.report.matchup import player_lines

    rows = player_lines(_prop_sim(), _roster(), "DAL")
    assert [r.position for r in rows] == ["QB", "RB", "WR", "TE"]
    # The starter, not the backup: each slot goes to the biggest projection.
    assert [r.name for r in rows] == [
        "Dak Prescott", "Javonte Williams", "CeeDee Lamb", "Jake Ferguson"]
    # ...and the third receiver does not displace the tight end.
    assert "Third Receiver" not in {r.name for r in rows}
    # A team's rows never reach across the line of scrimmage.
    assert "Wrong Team" not in {r.name for r in rows}


def test_player_lines_round_yards_whole_and_rates_to_a_decimal() -> None:
    from velocity.report.matchup import player_lines

    rows = {r.position: r for r in player_lines(_prop_sim(), _roster(), "DAL")}
    assert (rows["QB"].stat, rows["QB"].value) == ("PASS YDS", "271")
    assert (rows["QB"].stat2, rows["QB"].value2) == ("PASS TD", "1.8")
    assert (rows["RB"].stat, rows["RB"].value) == ("RUSH YDS", "68")
    assert (rows["RB"].stat2, rows["RB"].value2) == ("TOT TD", "0.6")
    assert (rows["WR"].stat, rows["WR"].value) == ("REC YDS", "88")
    assert (rows["WR"].stat2, rows["WR"].value2) == ("REC", "6.9")


def test_player_lines_leave_out_a_market_the_model_does_not_price() -> None:
    from velocity.report.matchup import player_lines

    # No pass_tds for the quarterback: the row keeps its yardage and says
    # nothing else, rather than reaching past the sim for a raw projection.
    sim = _Sim({("qb1", "pass_yards"): 271.4})
    rows = player_lines(sim, _roster(), "DAL")
    assert len(rows) == 1
    assert rows[0].stat2 is None and rows[0].value2 is None


def test_player_lines_without_a_sim_or_roster_are_empty() -> None:
    from velocity.report.matchup import player_lines

    assert player_lines(None, _roster(), "DAL") == ()
    assert player_lines(_prop_sim(), None, "DAL") == ()
    assert player_lines(_prop_sim(), _roster(), "NOBODY") == ()


def _social(*, spread_home: float | None = -3.5, total: float | None = 55.5,
            fair_spread: float = -6.0) -> object:
    """A SocialCard as the slate builds it, in the BOARD's sign convention.

    ``spread_home``/``fair_spread`` are negative when the home side is favored,
    which is what the market view and the wide card both carry.
    """
    from velocity.report.social import MarketView, SocialCard

    return SocialCard(
        game_id="g1", away_name="Dallas Cowboys", home_name="Detroit Lions",
        away_code="DAL", home_code="DET",
        kickoff=pd.Timestamp("2025-12-07 13:00"),
        p_home_win=0.685, mu_away=22.3, mu_home=28.6,
        fair_spread=fair_spread, fair_total=51.0,
        total_points_pmf={}, n_sims=20_000,
        market_view=MarketView(spread_home=spread_home, total=total),
        away_color="#003594", home_color="#0076B6",
    )


class _Proj:
    def __init__(self, margin: np.ndarray, total: np.ndarray) -> None:
        self.sim = type("S", (), {"margin": margin, "total": total})()


def _projection() -> _Proj:
    rng = np.random.default_rng(3)
    return _Proj(rng.normal(6.3, 13.0, 5_000), rng.normal(51.0, 10.0, 5_000))


def _build(**kw: object) -> list[MatchupCard]:
    from velocity.report.matchup import build_matchup_cards

    card = kw.pop("card", _social())
    return build_matchup_cards(
        [card], {"g1": _projection()},  # type: ignore[list-item,arg-type]
        _games(), _plays(), week_label="Week 14",
        props_by_game={"g1": _prop_sim()}, roster=_roster(),
        generated_at=pd.Timestamp("2026-09-16 22:44"),
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.usefixtures("spread_rule")
def test_builder_flips_both_spreads_into_the_cards_convention() -> None:
    # The board has DET -3.5 and the model has DET -6.0; on this card BOTH are
    # stated positive-means-home-favored so the two subtract directly. Carrying
    # either through unflipped prints the other team's side.
    built = _build()[0]
    assert built.market.spread_home == 3.5
    assert built.fair_spread_home == 6.0
    assert built.market_spread_label() == "DET -3.5"
    assert built.spread_label() == "DET -6.0"
    assert built.spread_lean().label == "DET -3.5"


def test_builder_flips_an_away_favorite_too() -> None:
    built = _build(card=_social(spread_home=3.5, fair_spread=6.0))[0]
    assert built.market.spread_home == -3.5
    assert built.market_spread_label() == "DAL -3.5"
    assert built.spread_label() == "DAL -6.0"


def test_builder_carries_the_total_through_unchanged() -> None:
    # A total has no sides to get backwards, so it is copied, not flipped.
    assert _build()[0].market.total == 55.5


def test_builder_fills_identity_record_form_and_ranks() -> None:
    built = _build(team_names={"DAL": "DAL", "DET": "DET"})[0]
    assert (built.away.city, built.away.nickname) == ("Dallas", "Cowboys")
    assert built.away.record == "1-1-1"       # W, L, T in the fixture games
    assert [g.result for g in built.away.last3] == ["W", "L", "T"]
    assert built.away.ranks["off"].rank == 1
    assert built.home.ranks["def"].rank == built.home.ranks["def"].of
    assert built.away.color == "#003594"
    assert [r.name for r in built.away.projections][0] == "Dak Prescott"


def test_builder_keeps_the_projection_and_the_stamp() -> None:
    built = _build()[0]
    assert (built.mu_away, built.mu_home) == (22.3, 28.6)
    assert built.p_home_win == 0.685
    assert built.week_label == "Week 14"
    # The sim count is counted off the draws the curves use, not copied from
    # the social card, so the number under a panel always describes that panel.
    assert built.n_sims == 5_000
    assert built.stamp() == "GENERATED 16 SEP 2026 · 22:44 UTC"
    assert built.kickoff == pd.Timestamp("2025-12-07 13:00")
    # Both distributions are real mass functions over the sim's own draws.
    assert abs(sum(built.margin_pmf.values()) - 1.0) < 1e-9
    assert abs(sum(built.total_pmf.values()) - 1.0) < 1e-9


def test_builder_derives_notes_but_lets_the_caller_override() -> None:
    assert _build(notes_by_game={"g1": ("a supplied note",)})[0].notes == (
        "a supplied note",)
    # Derived notes only ever restate ranks that are on the card.
    for note in _build()[0].notes:
        assert "ranks" in note


def test_builder_without_a_market_still_builds() -> None:
    built = _build(card=_social(spread_home=None, total=None))[0]
    assert built.market.spread_home is None
    assert not built.spread_lean().fired
    assert built.market_spread_label() == "—"


def test_builder_without_games_plays_or_props_still_builds() -> None:
    from velocity.report.matchup import build_matchup_cards

    built = build_matchup_cards([_social()], {"g1": _projection()})[0]  # type: ignore[list-item,arg-type]
    assert built.away.record == ""
    assert built.away.last3 == ()
    assert built.away.ranks == {}
    assert built.away.projections == ()
    assert built.market.spread_home == 3.5  # the market survives on its own


def test_builder_skips_a_card_with_no_projection() -> None:
    from velocity.report.matchup import build_matchup_cards

    assert build_matchup_cards([_social()], {}) == []


# --- the slate wiring ------------------------------------------------------

def test_roster_from_projections_keeps_only_identity() -> None:
    from velocity.report.matchup import roster_from_projections

    fp = pd.DataFrame([
        {"player_id": "1", "player_name": "Dak Prescott", "position": "QB",
         "team": "DAL", "stat": "pass_yds", "value": 271.0},
        {"player_id": "1", "player_name": "Dak Prescott", "position": "QB",
         "team": "DAL", "stat": "pass_tds", "value": 1.8},
        {"player_id": "2", "player_name": "CeeDee Lamb", "position": "WR",
         "team": "DAL", "stat": "rec_yds", "value": 88.0},
    ])
    roster = roster_from_projections(fp)
    # One row per player, not per stat — the sim is keyed by player.
    assert list(roster["player_name"]) == ["Dak Prescott", "CeeDee Lamb"]
    assert set(roster.columns) == {"player_key", "player_name", "position", "team"}


def test_roster_keys_match_the_sims_keys() -> None:
    # The roster is only useful if its keys are the sim's keys; they are both
    # built with player_key, and this pins that they stay that way.
    from velocity.models.props_football import player_key
    from velocity.report.matchup import roster_from_projections

    fp = pd.DataFrame([{"player_id": "17", "player_name": "Jared Goff",
                        "position": "QB", "team": "DET", "stat": "pass_yds",
                        "value": 258.0}])
    assert roster_from_projections(fp)["player_key"].iloc[0] == player_key(
        "17", "Jared Goff")


def _schedule() -> pd.DataFrame:
    played = _games()
    upcoming = pd.DataFrame([
        {"season": 2025, "week": 14, "kickoff": pd.Timestamp("2025-12-07"),
         "home_team": "DET", "away_team": "DAL",
         "home_score": None, "away_score": None},
        {"season": 2025, "week": 14, "kickoff": pd.Timestamp("2025-12-07"),
         "home_team": "KC", "away_team": "GB",
         "home_score": None, "away_score": None},
    ])
    return pd.concat([played, upcoming], ignore_index=True)


def test_slate_week_label_reads_the_schedule_not_a_count() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_live_slate", Path(__file__).parent.parent / "scripts" / "run_live_slate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Three completed games sit in weeks 11-13, but the week being PRICED is
    # the one with no finals yet.
    assert module._slate_week_label(_schedule()) == "Week 14"
    # The committed frames carry only completed games, so the fallback has to
    # work: newest completed week (13) plus one.
    assert module._slate_week_label(_games()) == "Week 14"
    # A bye cannot shift it, because the max is read rather than the weeks
    # counted -- weeks 11 and 13 with nothing in 12 still says 14.
    bye = _games()[_games()["week"] != 12]
    assert module._slate_week_label(bye) == "Week 14"
    # No week column, or no frame at all: say nothing rather than guess, and
    # the masthead drops the separator.
    assert module._slate_week_label(_games().drop(columns=["week"])) == ""
    assert module._slate_week_label(None) == ""
    assert module._slate_week_label(pd.DataFrame()) == ""


def test_masthead_without_a_week_has_no_dangling_separator(tmp_path: Path) -> None:
    # Rendering is the only check that matters here; the assertion is that a
    # blank label does not reach the frame as "NFL · ".
    from velocity.report import matchup_png

    captured: list[str] = []
    real = matchup_png._text

    def spy(fig: object, x: float, y: float, s: str, **kw: object) -> None:
        captured.append(s)
        real(fig, x, y, s, **kw)  # type: ignore[arg-type]

    matchup_png._text = spy  # type: ignore[assignment]
    try:
        matchup_png.render_matchup_card(
            replace(_card(), week_label=""), tmp_path / "a.png")
    finally:
        matchup_png._text = real  # type: ignore[assignment]
    assert "NFL" in captured
    assert not any(s.endswith(" · ") for s in captured)


def test_the_card_carries_no_confidence_score() -> None:
    """Measured and declined, not overlooked.

    Across 15,731 leak-safe walk-forward projections nothing the model knows
    about itself predicts how far it lands from the final (|r| < 0.04 on every
    candidate), because the sim's dispersion is near-constant by construction
    and so is not a per-game uncertainty estimate at all. A 0-10 score on that
    basis would sit near one value forever while reading as meaning. This pins
    the absence so it is a decision rather than a gap someone fills in later
    without redoing the measurement.
    """
    from dataclasses import fields

    assert "confidence" not in {f.name for f in fields(MatchupCard)}


# --- week 1, when "season to date" is empty --------------------------------

def _two_season_plays() -> pd.DataFrame:
    """Last season has plays; the new season has kicked off but played none."""
    rows = []
    for season in (2025,):
        for epa, pos, deft, kind in (
            (0.4, "DAL", "DET", "pass"), (0.1, "DAL", "DET", "run"),
            (0.2, "DET", "DAL", "pass"), (-0.1, "DET", "DAL", "run"),
        ):
            rows.append({"season": season, "week": 1, "posteam": pos,
                         "defteam": deft, "play_type": kind, "epa": epa})
    return pd.DataFrame(rows)


def test_rankable_season_falls_back_when_the_new_one_is_empty() -> None:
    from velocity.report.matchup import rankable_season

    plays = _two_season_plays()
    # 2026 has kicked off but nothing has been played: rank on 2025 rather than
    # render four labelled tracks with nothing on them.
    assert rankable_season(plays, 2026) == 2025
    # A season with its own plays ranks on itself.
    assert rankable_season(plays, 2025) == 2025
    # Two seasons back is not reached for -- that is not "recent form".
    assert rankable_season(plays, 2027) is None
    assert rankable_season(None, 2026) is None
    assert rankable_season(pd.DataFrame(), 2026) is None


def test_the_panel_says_which_season_the_ranks_are_from() -> None:
    from dataclasses import replace as dc_replace

    card = _card()
    # Same season: the usual caption.
    assert dc_replace(card, season=2026, ranks_season=2026).ranks_caption(32) == (
        "1 = best of 32 · season to date")
    # Borrowed from last year: say so, so the weakness is visible rather than
    # hidden behind a phrase that would be false.
    assert dc_replace(card, season=2026, ranks_season=2025).ranks_caption(32) == (
        "1 = best of 32 · 2025 season")
    # Nothing known: fall back to the neutral phrasing rather than inventing.
    assert dc_replace(card, season=None, ranks_season=None).ranks_caption(18) == (
        "1 = best of 18 · season to date")


def _week_one_games() -> pd.DataFrame:
    """Last season complete, the new one not started -- a week 1 slate's world."""
    return pd.DataFrame([
        {"season": 2025, "week": 17, "kickoff": pd.Timestamp("2025-12-28"),
         "home_team": "DET", "away_team": "DAL", "home_score": 24, "away_score": 20},
        {"season": 2025, "week": 16, "kickoff": pd.Timestamp("2025-12-21"),
         "home_team": "DAL", "away_team": "PHI", "home_score": 17, "away_score": 27},
    ])


def test_builder_ranks_a_week_one_card_off_last_season() -> None:
    from dataclasses import replace as dc_replace

    from velocity.report.matchup import build_matchup_cards

    # A September kickoff with nothing played yet: everything the card can say
    # comes from last year, and it has to say so.
    card = dc_replace(_social(), kickoff=pd.Timestamp("2026-09-10 13:00"))
    built = build_matchup_cards(
        [card], {"g1": _projection()},  # type: ignore[list-item,arg-type]
        _week_one_games(), _two_season_plays(),
    )[0]
    assert built.season == 2026
    assert built.ranks_season == 2025
    assert built.away.ranks, "week 1 must still get ranks, from last season"
    assert built.ranks_caption(32) == "1 = best of 32 · 2025 season"
    # ...and the record is stamped rather than passed off as this season's.
    assert built.away.record.startswith("2025: ")


def test_a_mid_season_card_stamps_nothing() -> None:
    from velocity.report.matchup import build_matchup_cards

    # The slate's season and the season with finals agree from week 2 on, so
    # neither the record nor the caption carries a year.
    built = build_matchup_cards(
        [_social()], {"g1": _projection()},  # type: ignore[list-item,arg-type]
        _games(), _plays(),
    )[0]
    assert built.season == built.ranks_season == 2025
    assert ":" not in built.away.record
    assert built.ranks_caption(32) == "1 = best of 32 · season to date"


@pytest.mark.parametrize(("kickoff", "season"), [
    ("2026-09-10", 2026),   # September: the season named for this year
    ("2026-12-28", 2026),
    ("2027-01-11", 2026),   # January playoff game belongs to the 2026 season
    ("2027-02-08", 2026),
])
def test_slate_season_puts_the_postseason_in_its_own_year(
        kickoff: str, season: int) -> None:
    from dataclasses import replace as dc_replace

    from velocity.report.matchup import slate_season

    card = dc_replace(_social(), kickoff=pd.Timestamp(kickoff))
    assert slate_season([card]) == season  # type: ignore[list-item]
    assert slate_season([]) is None
