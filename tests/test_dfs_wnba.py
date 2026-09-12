"""The WNBA DFS vertical — DK's roster, and a rate times the minutes she plays.

The roster spec came first, read off DK's own rules API with no WNBA board
running anywhere: the template is public whether or not a slate is live. What
was missing was anything to put in it. A WNBA roster runs eight or nine deep,
so the minute split is the largest single term in any projection, and these
pin that the model is built that way — and that the one input no API will
confirm, DK's basketball scoring, is at least arithmetically sound and has a
check waiting for the first live board.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from velocity.models.dfs_wnba import (
    DK_WNBA_POINTS,
    WnbaDfsModel,
    scoring_disagreement,
    wnba_dk_points,
)


def _box(rows: list[dict]) -> pd.DataFrame:
    """A player-box frame in the banked shape, defaults filled in."""
    base = {"game_id": "1", "game_date": pd.Timestamp("2026-06-01"),
            "athlete_id": "1", "athlete_display_name": "A Player",
            "athlete_position_abbreviation": "G", "team_abbreviation": "NY",
            "opponent_team_abbreviation": "LV", "home_away": "home",
            "season": 2026, "season_type": 2, "starter": True,
            "did_not_play": False, "minutes": 30.0, "points": 0.0,
            "rebounds": 0.0, "assists": 0.0, "steals": 0.0, "blocks": 0.0,
            "turnovers": 0.0, "three_point_field_goals_made": 0.0}
    return pd.DataFrame([{**base, **row} for row in rows])


# --- the scoring line --------------------------------------------------------


def test_the_counting_line_scores_at_dks_weights() -> None:
    # 20 pts, 6 reb, 4 ast, 2 stl, 1 blk, 3 TO, 2 threes:
    # 20 + 7.5 + 6 + 4 + 2 - 1.5 + 1 = 39.0, and no bonus (only points hit 10).
    frame = _box([{"points": 20, "rebounds": 6, "assists": 4, "steals": 2,
                   "blocks": 1, "turnovers": 3,
                   "three_point_field_goals_made": 2}])
    assert float(wnba_dk_points(frame).iloc[0]) == 39.0


def test_a_double_double_pays_its_bonus() -> None:
    # 18 + 12.5 = 30.5 counting, plus the 1.5 for two categories at ten.
    frame = _box([{"points": 18, "rebounds": 10}])
    assert float(wnba_dk_points(frame).iloc[0]) == 32.0


def test_a_triple_double_pays_both_bonuses() -> None:
    """DK stacks them: the triple-double does not replace the double-double."""
    frame = _box([{"points": 15, "rebounds": 11, "assists": 10}])
    counting = 15 + 11 * 1.25 + 10 * 1.5
    assert float(wnba_dk_points(frame).iloc[0]) == counting + 1.5 + 3.0


def test_nine_of_something_is_not_a_double() -> None:
    frame = _box([{"points": 20, "rebounds": 9}])
    assert float(wnba_dk_points(frame).iloc[0]) == 20 + 9 * 1.25


def test_a_thinner_historical_frame_scores_what_it_has() -> None:
    # A column the release does not ship contributes zero rather than raising.
    frame = _box([{"points": 12}])[["athlete_id", "points", "minutes"]]
    assert float(wnba_dk_points(frame).iloc[0]) == 12.0


def test_the_weights_are_the_ones_the_model_is_built_on() -> None:
    # A guard against an edit that silently rescales every projection.
    assert DK_WNBA_POINTS["points"] == 1.0
    assert DK_WNBA_POINTS["rebounds"] == 1.25
    assert DK_WNBA_POINTS["assists"] == 1.5
    assert DK_WNBA_POINTS["steals"] == DK_WNBA_POINTS["blocks"] == 2.0
    assert DK_WNBA_POINTS["turnovers"] == -0.5


def test_the_scoring_check_is_ready_for_the_first_live_board() -> None:
    """These constants are the one input here no API will confirm.

    DK's rules endpoint carries the roster template and no scoring, the help
    page renders client-side, and every scoring-shaped path 404s. What DK does
    publish, on any live board, is its own fantasy-points-per-game — so this
    compares the two the moment a WNBA board exists.
    """
    projected = pd.DataFrame({"player_name": ["A'ja Wilson", "Missing Person"],
                              "points": [43.8, 10.0]})
    board = pd.DataFrame({"player_name": ["a'ja wilson "], "fppg": [42.1]})
    gaps = scoring_disagreement(projected, board)
    assert len(gaps) == 1
    assert abs(float(gaps["gap"].iloc[0]) - 1.7) < 1e-9
    # No board, no check — and no pretending there was one.
    assert scoring_disagreement(projected, pd.DataFrame()).empty


# --- the model ---------------------------------------------------------------


def test_minutes_are_the_biggest_term() -> None:
    """Two players with the same per-minute value, one playing three times more."""
    rows = []
    for game in range(20):
        day = pd.Timestamp("2026-06-01") + pd.Timedelta(days=game)
        rows.append({"game_id": str(game), "game_date": day, "athlete_id": "starter",
                     "athlete_display_name": "Starter", "minutes": 33.0, "points": 22.0})
        rows.append({"game_id": str(game), "game_date": day, "athlete_id": "reserve",
                     "athlete_display_name": "Reserve", "minutes": 11.0, "points": 7.0})
    model = WnbaDfsModel.fit(_box(rows))
    # Not the raw 3x: the one-game prior pulls both toward the league mean, so
    # the projection states the gap without quite believing all of it.
    assert model.project("starter") > 2.2 * model.project("reserve")


def test_a_thin_sample_prices_near_her_position_not_off_her_fluke() -> None:
    # One enormous game in nine minutes must not out-project a season of work.
    rows = [{"game_id": str(g), "game_date": pd.Timestamp("2026-06-01") + pd.Timedelta(days=g),
             "athlete_id": "regular", "athlete_display_name": "Regular",
             "minutes": 30.0, "points": 15.0} for g in range(25)]
    rows.append({"game_id": "99", "game_date": pd.Timestamp("2026-07-01"),
                 "athlete_id": "callup", "athlete_display_name": "Call Up",
                 "minutes": 9.0, "points": 30.0, "rebounds": 12.0})
    model = WnbaDfsModel.fit(_box(rows))
    assert model.project("callup") < model.project("regular")


def test_a_player_the_bank_has_never_seen_prices_at_her_position() -> None:
    rows = [{"game_id": str(g), "game_date": pd.Timestamp("2026-06-01") + pd.Timedelta(days=g),
             "athlete_id": "known", "athlete_display_name": "Known",
             "minutes": 28.0, "points": 14.0} for g in range(15)]
    model = WnbaDfsModel.fit(_box(rows))
    unknown = model.project("nobody-at-all", position="G")
    assert unknown > 0
    assert abs(unknown - model.rate_prior["G"] * model.minutes_prior["G"]) < 1e-9


def test_a_projection_may_not_see_the_game_it_is_projecting() -> None:
    """``before`` is what keeps a backtest honest."""
    rows = [{"game_id": str(g), "game_date": pd.Timestamp("2026-06-01") + pd.Timedelta(days=g),
             "athlete_id": "p", "athlete_display_name": "P",
             "minutes": 30.0, "points": 10.0} for g in range(10)]
    rows.append({"game_id": "big", "game_date": pd.Timestamp("2026-07-01"),
                 "athlete_id": "p", "athlete_display_name": "P",
                 "minutes": 38.0, "points": 50.0})
    box = _box(rows)
    before = WnbaDfsModel.fit(box, before=pd.Timestamp("2026-07-01"))
    after = WnbaDfsModel.fit(box)
    assert after.project("p") > before.project("p"), "the huge game must move it"


def test_a_player_who_did_not_dress_contributes_no_rate() -> None:
    rows = [{"game_id": str(g), "game_date": pd.Timestamp("2026-06-01") + pd.Timedelta(days=g),
             "athlete_id": "p", "athlete_display_name": "P",
             "minutes": 30.0, "points": 18.0} for g in range(12)]
    with_dnp = rows + [{"game_id": "dnp", "game_date": pd.Timestamp("2026-07-01"),
                        "athlete_id": "p", "athlete_display_name": "P",
                        "minutes": 0.0, "points": 0.0}]
    assert (WnbaDfsModel.fit(_box(rows)).project("p")
            == WnbaDfsModel.fit(_box(with_dnp)).project("p"))


def test_an_empty_bank_projects_nothing_rather_than_raising() -> None:
    model = WnbaDfsModel.fit(pd.DataFrame())
    assert model.rate_of == {} and model.projections(pd.DataFrame()).empty


def test_the_projection_frame_is_the_shape_the_optimizer_eats() -> None:
    rows = [{"game_id": str(g), "game_date": pd.Timestamp("2026-06-01") + pd.Timedelta(days=g),
             "athlete_id": "p", "athlete_display_name": "P",
             "minutes": 30.0, "points": 18.0} for g in range(12)]
    frame = WnbaDfsModel.fit(_box(rows)).projections(_box(rows))
    assert list(frame.columns) == ["player_id", "player_name", "team", "position", "points"]
    assert len(frame) == 1 and frame["points"].iloc[0] > 0


# --- the wiring --------------------------------------------------------------


def test_the_wnba_roster_is_dks_own_six_player_shape() -> None:
    """Game type 37, read with no WNBA board running.

    The template is public whether or not a slate is, so this is DK's spec
    rather than a reconstruction: two guards, three forwards, and a utility
    slot either can fill, under the same $50,000 cap as every other classic.
    """
    from velocity.dfs.optimizer import SALARY_CAP, WNBA_CLASSIC

    assert WNBA_CLASSIC.slots == ("G", "G", "F", "F", "F", "UTIL")
    assert WNBA_CLASSIC.base_counts == {"G": 2, "F": 3}
    assert WNBA_CLASSIC.flex == (("UTIL", ("G", "F")),)
    assert len(WNBA_CLASSIC.slots) == 6
    assert SALARY_CAP == 50_000


def test_the_league_is_priceable_now() -> None:
    # It was not: the spec shipped alone because nothing could score a board.
    from velocity.dfs.optimizer import WNBA_CLASSIC
    from velocity.dfs.pipeline import LEAGUE_SPECS

    spec, scorer = LEAGUE_SPECS["wnba"]
    assert spec is WNBA_CLASSIC and callable(scorer)


def test_a_centre_on_the_board_is_a_forward_to_draftkings() -> None:
    """ESPN spells C; DK's WNBA board runs two positions and a centre is an F.

    Without the fold every post player falls out of the pool, which is most of
    the league's production.
    """
    from velocity.dfs.optimizer import WNBA_CLASSIC
    from velocity.dfs.pipeline import normalize_positions

    board = pd.DataFrame({"position": ["C", "G/F", "G", "F"],
                          "player_name": list("abcd")})
    out = normalize_positions(board, WNBA_CLASSIC)
    assert list(out["position"]) == ["F", "G", "G", "F"]


def test_a_solvable_board_solves_into_a_legal_six() -> None:
    from velocity.dfs.optimizer import (
        SALARY_CAP,
        WNBA_CLASSIC,
        build_lineup,
        lineup_pool,
    )
    from velocity.dfs.pipeline import eligible_board, normalize_positions
    from velocity.dfs.scoring import dk_expected_points_wnba

    rng = np.random.default_rng(5)
    rows = []
    for i in range(24):
        for game in range(12):
            rows.append({
                "game_id": str(game), "athlete_id": f"p{i}",
                "game_date": pd.Timestamp("2026-06-01") + pd.Timedelta(days=game),
                "athlete_display_name": f"Player {i}",
                "athlete_position_abbreviation": "G" if i % 2 else "F",
                "team_abbreviation": "NY" if i % 3 else "LV",
                "minutes": 12.0 + i, "points": 4.0 + i * 0.7,
                "rebounds": float(rng.integers(0, 8)),
                "assists": float(rng.integers(0, 6)),
            })
    points = dk_expected_points_wnba(_box(rows))
    board = points.assign(
        salary=(3000 + points["points"] * 190).clip(3000, 11500).round(-2).astype(int),
        draft_group_id="1", competition="NY @ LV")
    board = eligible_board(normalize_positions(board, WNBA_CLASSIC), WNBA_CLASSIC)
    lineup = build_lineup(lineup_pool(board, points), spec=WNBA_CLASSIC)
    assert lineup is not None
    assert [s.slot for s in lineup.slots] == ["G", "G", "F", "F", "F", "UTIL"]
    assert lineup.total_salary <= SALARY_CAP
    assert len({s.player_name for s in lineup.slots}) == 6
