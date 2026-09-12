"""The college DFS vertical — the fold, the window, and what the source gets wrong.

NCAAF was the only in-season sport with a roster spec, daily salary collection
and nothing at all to price a board with: FantasyPros serves no college
players, so the builder filtered its projection frame to zero rows and exited
cleanly every run. cfbfastR supplies the missing half keyless, but it is
derived from ESPN's play text, and these pin the three places that matters.
"""

from __future__ import annotations

import pandas as pd
from velocity.ingest.cfb_players import (
    CFB_POSITIONS,
    infer_positions,
    player_games,
    season_coverage,
)
from velocity.models.dfs_ncaaf import RECENT_GAMES, dk_expected_points_ncaaf, recent_games


def _plays(rows: list[dict]) -> pd.DataFrame:
    """A cfbfastR-shaped play frame, every role column present and null."""
    roles = ["completion", "incompletion", "interception_thrown", "rush",
             "reception", "target", "touchdown", "fumble", "field_goal_made"]
    base: dict[str, object] = {"game_id": "g1", "team": "Georgia",
                               "opponent": "Alabama", "season": 2024, "week": 1,
                               "team_score": 28, "completion_yds": 0.0,
                               "rush_yds": 0.0, "reception_yds": 0.0}
    for role in roles:
        base[f"{role}_player_id"] = None
        base[f"{role}_player"] = None
    return pd.DataFrame([{**base, **row} for row in rows])


# --- the fold ----------------------------------------------------------------


def test_a_passing_touchdown_is_both_a_passing_and_a_receiving_one() -> None:
    """The source names the scorer inconsistently; football does not.

    On a passing touchdown cfbfastR's ``touchdown_player`` is the passer 57% of
    the time and the receiver 43% — whichever ESPN's text put first. So the
    fold never reads it to decide whose touchdown it was: a completion on a
    scoring play is a passing touchdown for the passer and a receiving one for
    the receiver, always.
    """
    frame = player_games(_plays([{
        "completion_player_id": "qb", "completion_player": "A QB",
        "reception_player_id": "wr", "reception_player": "A WR",
        "completion_yds": 25.0, "reception_yds": 25.0,
        # Named as the passer on this play; the receiver still gets his.
        "touchdown_player_id": "qb", "touchdown_player": "A QB",
    }]))
    by = frame.set_index("player_id")
    assert by.loc["qb", "pass_tds"] == 1.0 and by.loc["qb", "receiving_tds"] == 0.0
    assert by.loc["wr", "receiving_tds"] == 1.0 and by.loc["wr", "pass_tds"] == 0.0


def test_a_pick_six_is_not_a_passing_touchdown() -> None:
    """Verified safe in the source: an interception play carries no completion."""
    frame = player_games(_plays([{
        "interception_thrown_player_id": "qb", "interception_thrown_player": "A QB",
        "touchdown_player_id": "db", "touchdown_player": "A DB",
    }]))
    by = frame.set_index("player_id")
    assert by.loc["qb", "interceptions"] == 1.0
    assert by.loc["qb", "pass_tds"] == 0.0
    assert by.loc["qb", "attempts"] == 1.0


def test_a_target_is_a_reception_plus_an_incompletion_aimed_at_him() -> None:
    # The source records ``target_player`` ONLY on an incompletion — it is
    # empty on every completion — so a reception has to count itself.
    frame = player_games(_plays([
        {"completion_player_id": "qb", "completion_player": "A QB",
         "reception_player_id": "wr", "reception_player": "A WR",
         "reception_yds": 12.0},
        {"incompletion_player_id": "qb", "incompletion_player": "A QB",
         "target_player_id": "wr", "target_player": "A WR"},
    ]))
    by = frame.set_index("player_id")
    assert by.loc["wr", "targets"] == 2.0
    assert by.loc["wr", "receptions"] == 1.0
    assert by.loc["qb", "attempts"] == 2.0


def test_the_dk_line_is_scored_the_way_the_nfl_board_scores_it() -> None:
    # DK's college scoring IS its NFL scoring, so the fold speaks that
    # vocabulary and the NFL scorer prices it: 100 rushing yards is 10 points
    # plus the +3 milestone, and the touchdown is 6.
    frame = player_games(_plays([{
        "rush_player_id": "rb", "rush_player": "A RB", "rush_yds": 100.0,
        "touchdown_player_id": "rb", "touchdown_player": "A RB",
    }]))
    assert float(frame["dk_points"].iloc[0]) == 10.0 + 3.0 + 6.0


def test_an_empty_release_folds_to_an_empty_frame() -> None:
    frame = player_games(pd.DataFrame())
    assert frame.empty and "dk_points" in frame.columns


# --- who a player is ---------------------------------------------------------


def test_a_position_is_read_off_how_his_team_used_him() -> None:
    """The source ships no position column, and the usage states it plainly."""
    frame = pd.DataFrame([
        {"player_id": "qb", "attempts": 30.0, "carries": 8.0, "targets": 0.0},
        {"player_id": "rb", "attempts": 0.0, "carries": 20.0, "targets": 3.0},
        {"player_id": "wr", "attempts": 0.0, "carries": 1.0, "targets": 9.0},
    ])
    assert list(infer_positions(frame)) == ["QB", "RB", "WR"]


def test_a_quarterback_who_runs_is_still_a_quarterback() -> None:
    # And a back who throws once on a trick play is still a back.
    frame = pd.DataFrame([
        {"player_id": "dual", "attempts": 18.0, "carries": 16.0, "targets": 0.0},
        {"player_id": "wildcat", "attempts": 1.0, "carries": 22.0, "targets": 2.0},
    ])
    assert list(infer_positions(frame)) == ["QB", "RB"]


def test_a_player_with_no_usage_at_all_gets_no_position() -> None:
    frame = pd.DataFrame([{"player_id": "x", "attempts": 0.0, "carries": 0.0,
                           "targets": 0.0}])
    assert list(infer_positions(frame)) == [""]


def test_only_the_three_positions_dk_rosters_are_used() -> None:
    # DK lists college tight ends as receivers and has no defensive slot.
    assert CFB_POSITIONS == ("QB", "RB", "WR")


# --- coverage, which is the thing to watch -----------------------------------


def test_a_season_the_release_has_not_finished_looks_like_nobody_scored() -> None:
    """Coverage is what tells a thin season from a low-scoring one.

    cfbfastR fills progressively: the banked seasons come in at 0.82 (2023)
    and 0.75 (2024), while 2025 sits at 0.46 and the current 2026 at 0.24. A
    projection fitted on an unfilled season prices every player at nothing,
    so the build refuses rather than shipping that.
    """
    covered = _plays([{"touchdown_player_id": "a", "touchdown_player": "A",
                       "team_score": 14},
                      {"touchdown_player_id": "b", "touchdown_player": "B",
                       "team_score": 14}])
    assert season_coverage(covered) == 1.0
    # The same game with its scoring unattributed reads as uncovered.
    thin = _plays([{"rush_player_id": "a", "rush_player": "A", "team_score": 28}])
    assert season_coverage(thin) == 0.0
    assert season_coverage(pd.DataFrame()) == 0.0


# --- the model ---------------------------------------------------------------


def _bank(n_games: int = 10, dk: float = 20.0, player: str = "p") -> pd.DataFrame:
    rows = []
    for week in range(1, n_games + 1):
        rows.append({
            "season": 2024, "week": week, "game_id": f"g{week}",
            "player_id": player, "player_name": "A Player", "team": "Georgia",
            "opponent": "Alabama", "position": "RB", "attempts": 0.0,
            "pass_yards": 0.0, "pass_tds": 0.0, "interceptions": 0.0,
            "carries": 18.0, "rush_yards": dk * 10, "rush_tds": 0.0,
            "targets": 0.0, "receptions": 0.0, "receiving_yards": 0.0,
            "receiving_tds": 0.0, "dk_points": dk,
        })
    return pd.DataFrame(rows)


def test_the_window_reads_only_his_most_recent_games() -> None:
    bank = _bank(20)
    assert len(recent_games(bank, 6)) == 6
    assert list(recent_games(bank, 6)["week"]) == [15, 16, 17, 18, 19, 20]


def test_the_window_is_half_a_college_season() -> None:
    # Swept walk-forward over 29,217 player-games with a real interior
    # optimum: two is noise, forty is stale, six is the peak.
    assert RECENT_GAMES == 6


def test_a_projection_may_not_see_the_game_it_is_projecting() -> None:
    bank = _bank(8, dk=10.0)
    late = bank.copy()
    late.loc[late["week"] >= 7, "dk_points"] = 40.0
    early = dk_expected_points_ncaaf(late, before=(2024, 7))
    everything = dk_expected_points_ncaaf(late)
    assert float(everything["points"].iloc[0]) > float(early["points"].iloc[0])


def test_a_thin_sample_prices_toward_his_position() -> None:
    regulars = pd.concat([_bank(10, dk=8.0, player=f"r{i}") for i in range(6)],
                         ignore_index=True)
    oneoff = _bank(1, dk=60.0, player="flash")
    proj = dk_expected_points_ncaaf(pd.concat([regulars, oneoff], ignore_index=True))
    flash = float(proj.loc[proj["player_id"] == "flash", "points"].iloc[0])
    regular = float(proj.loc[proj["player_id"] == "r0", "points"].iloc[0])
    assert flash < 60.0, "one game must not become the projection"
    assert flash > regular, "but it is still evidence he is better"


def test_an_empty_bank_projects_nothing() -> None:
    assert dk_expected_points_ncaaf(pd.DataFrame()).empty
    assert dk_expected_points_ncaaf(_bank(2)[_bank(2)["position"] == "K"]).empty


def test_the_projection_frame_is_the_shape_the_optimizer_eats() -> None:
    proj = dk_expected_points_ncaaf(_bank(6))
    assert list(proj.columns) == ["player_id", "player_name", "team",
                                  "position", "points"]
    assert len(proj) == 1 and proj["points"].iloc[0] > 0


def test_the_league_is_priceable_now() -> None:
    # It was not: the scorer was the FantasyPros one, which serves no college
    # players, so the builder filtered to zero rows every single run.
    from velocity.dfs.optimizer import CFB_CLASSIC
    from velocity.dfs.pipeline import LEAGUE_SPECS

    spec, scorer = LEAGUE_SPECS["ncaaf"]
    assert spec is CFB_CLASSIC
    assert scorer(_bank(6)).shape[0] == 1
