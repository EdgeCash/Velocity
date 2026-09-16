"""Matchup card — the leans' arithmetic, the labels' signs, and a render smoke.

The sign of a lean is the one thing on this card that can be wrong *publicly*:
"DET +3.5" and "DET -3.5" are opposite bets, and the card goes to an account,
not a log file. So every direction of every market gets pinned here rather than
sampled. The render is an offline smoke — no asset cache, so no network — that
only asserts a real PNG of the right frame lands.
"""

from __future__ import annotations

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
from velocity.report.social import SPREAD_EDGE_PTS, TOTAL_EDGE_PTS

AWAY = TeamSide(code="DAL", city="Dallas", nickname="Cowboys", record="6-5-1",
                color="#003594")
HOME = TeamSide(code="DET", city="Detroit", nickname="Lions", record="7-5",
                color="#0076B6")


def _card(*, fair_spread_home: float = 3.5, fair_total: float = 48.0,
          spread_home: float | None = 3.5, total: float | None = 48.0,
          **kw: object) -> MatchupCard:
    return MatchupCard(
        game_id="g1", league="nfl", week_label="Week 14",
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
def test_spread_lean_states_the_side_at_its_own_number(
        fair: float, market: float, label: str) -> None:
    lean = _card(fair_spread_home=fair, spread_home=market).spread_lean()
    assert lean.fired
    assert lean.label == label


def test_spread_lean_holds_inside_the_bar() -> None:
    lean = _card(fair_spread_home=3.5 + SPREAD_EDGE_PTS - 0.1,
                 spread_home=3.5).spread_lean()
    assert not lean.fired
    assert lean.detail == "no edge"


def test_spread_lean_fires_exactly_at_the_bar() -> None:
    assert _card(fair_spread_home=3.5 + SPREAD_EDGE_PTS,
                 spread_home=3.5).spread_lean().fired


def test_spread_lean_without_a_market_says_so() -> None:
    lean = _card(spread_home=None).spread_lean()
    assert not lean.fired
    assert lean.detail == "no market"


def test_a_very_wide_gap_is_flagged_rather_than_amplified() -> None:
    # Our own graded record puts the worst closing-line value in the
    # highest-edge bucket, so the card marks the outlier instead of leaning in.
    lean = _card(fair_spread_home=3.5 + WIDE_GAP_PTS, spread_home=3.5).spread_lean()
    assert lean.fired and lean.wide


# --- the total lean --------------------------------------------------------

@pytest.mark.parametrize(("fair", "market", "label"), [
    (48.0 + TOTAL_EDGE_PTS, 48.0, "OVER 48"),
    (48.0 - TOTAL_EDGE_PTS, 48.0, "UNDER 48"),
])
def test_total_lean_names_the_side_at_the_posted_number(
        fair: float, market: float, label: str) -> None:
    lean = _card(fair_total=fair, total=market).total_lean()
    assert lean.fired
    assert lean.label == label


def test_total_lean_holds_inside_the_bar() -> None:
    assert not _card(fair_total=48.0 + TOTAL_EDGE_PTS - 0.1, total=48.0).total_lean().fired


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
        confidence={"spread": 6.4, "total": 7.1},
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
def test_split_name(full: str, city: str, nickname: str) -> None:
    from velocity.report.matchup import split_name

    assert split_name(full, "DAL") == (city, nickname)


@pytest.mark.parametrize(("n", "expected"), [
    (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"),
    (11, "11th"), (12, "12th"), (13, "13th"), (21, "21st"), (32, "32nd"),
])
def test_ordinal(n: int, expected: str) -> None:
    from velocity.report.matchup import _ordinal

    assert _ordinal(n) == expected


def test_unit_notes_only_speak_to_a_real_gap() -> None:
    from velocity.report.matchup import NOTE_GAP_MIN, unit_notes

    def side(code: str, off_pass: int, def_pass: int) -> TeamSide:
        return TeamSide(code=code, city=code, nickname=code, record="", color="#fff",
                        ranks={"off_pass": UnitRank(off_pass, 32, 0.1),
                               "def_pass": UnitRank(def_pass, 32, 0.1)})

    wide = unit_notes(side("DAL", 2, 30), side("DET", 20, 30))
    assert wide and "DAL" in wide[0] and "2nd" in wide[0] and "30th" in wide[0]

    narrow = unit_notes(side("DAL", 10, 10 + NOTE_GAP_MIN - 1),
                        side("DET", 10, 10 + NOTE_GAP_MIN - 1))
    assert narrow == ()


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
