"""The DFS surface's receipt: what the lineups that went out actually scored.

The optimizers are exact and the projections are backtested, but until this
nothing asked what a lineup DK actually paid on was worth. Three things have
to be right or the number flatters itself: a scratch has to score the zero DK
pays rather than being dropped, the captain's realized points have to carry
the same 1.5x his projection does, and a day's boards have to stay separate
entries rather than collapsing into one meaningless total.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.dfs.backtest import grade_lineup_frame, lineup_record

_SCRIPT = Path(__file__).parent.parent / "scripts" / "grade_dfs.py"
DAY = pd.Timestamp("2026-09-11 23:15:00")


def _grader():
    spec = importlib.util.spec_from_file_location("grade_dfs", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _index(**points: float) -> dict[tuple[str, object], dict[str, object]]:
    from velocity.dfs.backtest import norm

    return {
        (norm(name.replace("_", " ")), DAY.date()): {"actual": value}
        for name, value in points.items()
    }


def _lineup(*rows: tuple[str, str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"slot": slot, "player_name": name, "position": "OF", "team": "ATL",
             "salary": 5000, "points": points, "kickoff": DAY,
             "draft_group_id": "12345", "format": "showdown", "slate": "Night",
             "suffix": "Night"}
            for slot, name, points in rows
        ]
    )


def test_a_rostered_player_who_never_appeared_scores_the_zero_dk_pays() -> None:
    """Dropping him instead would hand every build a scratch-proof roster."""
    lineups = _lineup(("UTIL", "Ronald Acuna", 12.0), ("UTIL", "Ghost Player", 9.0))
    graded = grade_lineup_frame(lineups, _index(Ronald_Acuna=21.5))
    assert list(graded["actual"]) == [21.5, 0.0]
    # ...and the miss is stated, so a scratch is never read as a bad projection.
    assert list(graded["matched"]) == [True, False]


def test_the_captain_multiplier_applies_to_the_realized_points_too() -> None:
    # The banked projection for a CPT slot already carries the 1.5x, so the
    # actual must as well or every showdown entry reads as a miss.
    lineups = _lineup(("CPT", "Ronald Acuna", 18.0), ("UTIL", "Matt Olson", 8.0))
    graded = grade_lineup_frame(lineups, _index(Ronald_Acuna=20.0, Matt_Olson=6.0))
    assert list(graded["actual"]) == [30.0, 6.0]


def test_a_name_spelled_with_an_accent_still_joins() -> None:
    # DK spells "Sanchez", statsapi spells "Sánchez"; folding is what keeps
    # the player from silently scoring zero.
    lineups = _lineup(("UTIL", "Jesus Sanchez", 9.0))
    graded = grade_lineup_frame(lineups, {("jesussanchez", DAY.date()): {"actual": 14.0}})
    assert graded["matched"].all() and float(graded["actual"].iloc[0]) == 14.0


def test_each_slot_is_graded_against_its_own_game_day() -> None:
    """A board spanning midnight UTC must not grade both halves on one date."""
    late = pd.Timestamp("2026-09-12 02:10:00")
    lineups = _lineup(("UTIL", "Ronald Acuna", 12.0))
    lineups = pd.concat(
        [lineups, lineups.assign(player_name="Shohei Ohtani", kickoff=late)],
        ignore_index=True,
    )
    index = {
        ("ronaldacuna", DAY.date()): {"actual": 10.0},
        ("shoheiohtani", late.date()): {"actual": 30.0},
    }
    graded = grade_lineup_frame(lineups, index)
    assert list(graded["actual"]) == [10.0, 30.0]


def test_a_frame_with_no_kickoff_falls_back_to_the_slate_date() -> None:
    lineups = _lineup(("UTIL", "Ronald Acuna", 12.0)).assign(kickoff=None)
    graded = grade_lineup_frame(lineups, _index(Ronald_Acuna=17.0), slate_date=DAY)
    assert float(graded["actual"].iloc[0]) == 17.0
    # Without the fallback there is no date to key on, so nothing matches.
    assert not grade_lineup_frame(lineups, _index(Ronald_Acuna=17.0))["matched"].any()


def test_an_empty_frame_grades_to_an_empty_frame() -> None:
    graded = grade_lineup_frame(pd.DataFrame(), {})
    assert graded.empty
    assert lineup_record(graded).empty


def test_the_days_boards_stay_separate_entries() -> None:
    """One number for "the DFS day" would describe no roster anyone entered."""
    classic = _lineup(("UTIL", "Ronald Acuna", 12.0), ("UTIL", "Matt Olson", 8.0)).assign(
        format="classic", draft_group_id="999", slate="Main", suffix="")
    showdown = _lineup(("CPT", "Ronald Acuna", 18.0), ("UTIL", "Matt Olson", 8.0))
    graded = grade_lineup_frame(
        pd.concat([classic, showdown], ignore_index=True),
        _index(Ronald_Acuna=20.0, Matt_Olson=6.0),
    )
    record = lineup_record(graded).set_index("format")
    assert set(record.index) == {"classic", "showdown"}
    assert float(record.loc["classic", "realized"]) == 26.0
    assert float(record.loc["showdown", "realized"]) == 36.0
    assert float(record.loc["classic", "error"]) == 6.0
    assert int(record.loc["showdown", "n_matched"]) == 2


def test_the_record_counts_the_players_it_could_not_find() -> None:
    graded = grade_lineup_frame(
        _lineup(("UTIL", "Ronald Acuna", 12.0), ("UTIL", "Ghost Player", 9.0)),
        _index(Ronald_Acuna=21.5),
    )
    row = lineup_record(graded).iloc[0]
    assert (int(row["n_slots"]), int(row["n_matched"])) == (2, 1)


# --- the defense, which is nobody's name -------------------------------------


def test_a_defense_is_graded_by_its_team_not_its_name() -> None:
    """DK writes a club, the box score writes players — the name never joins.

    Left on the name lookup a DST scores 0.0 every single week, which is a
    ninth of every classic roster written off as a miss.
    """
    lineups = _lineup(("DST", "Falcons", 7.5))
    lineups = lineups.assign(position="DST", team="ATL")
    graded = grade_lineup_frame(
        lineups, {}, team_index={("atl", DAY.date()): {"actual": 12.0}})
    assert graded["matched"].all() and float(graded["actual"].iloc[0]) == 12.0


def test_a_defense_with_no_team_index_is_reported_missing_not_scored() -> None:
    lineups = _lineup(("DST", "Falcons", 7.5)).assign(position="DST", team="ATL")
    graded = grade_lineup_frame(lineups, _index(Falcons=12.0))
    assert not graded["matched"].any() and float(graded["actual"].iloc[0]) == 0.0


def test_the_realized_defense_scores_the_counting_stats_and_the_bracket() -> None:
    from velocity.dfs.dst import dst_actuals

    team_weeks = pd.DataFrame([{
        "season": 2026, "week": 1, "team": "SEA", "def_sacks": 3.0,
        "def_interceptions": 3, "def_fumbles": 1, "def_tds": 1,
        "special_teams_tds": 0, "def_safeties": 0, "def_punt_blocks": 0,
        "def_pat_blocks": 0, "def_fg_blocks": 0, "def_2pt_made": 0,
    }])
    schedule = pd.DataFrame([{
        "game_id": "2026_01_SEA_NE", "season": 2026, "week": 1,
        "kickoff": pd.Timestamp("2026-09-09 20:15:00"),
        "home_team": "SEA", "away_team": "NE",
        "home_score": 24.0, "away_score": 13.0,
    }])
    actual = dst_actuals(team_weeks, schedule)
    # 3 sacks + 3 INT (2 each) + 1 recovery (2) + 1 defensive TD (6) = 17,
    # plus the 7-13 points-allowed bracket (+4).
    assert float(actual["actual"].iloc[0]) == 21.0
    assert actual["team"].iloc[0] == "SEA"
    assert actual["kickoff"].iloc[0] == pd.Timestamp("2026-09-09 20:15:00")


def test_a_draftkings_team_code_finds_the_nflverse_one() -> None:
    """DK writes "LAR", every nflverse release writes "LA" — same defense."""
    from velocity.dfs.dst import dst_day_index

    actuals = pd.DataFrame([{"team": "LA", "game_id": "2026_01_SF_LA",
                             "kickoff": pd.Timestamp("2026-09-10 20:15:00"),
                             "actual": 9.0}])
    index = dst_day_index(actuals)
    day = pd.Timestamp("2026-09-10").date()
    assert index[("lar", day)]["actual"] == 9.0
    assert index[("la", day)] == index[("lar", day)]
    # A club with no alternate spelling still keys under its own code.
    plain = dst_day_index(actuals.assign(team="ATL"))
    assert set(plain) == {("atl", day)}


def test_a_defense_with_no_finished_game_is_dropped_rather_than_bracketed() -> None:
    """Points allowed IS most of the score; scoring without it invents +10."""
    from velocity.dfs.dst import dst_actuals

    team_weeks = pd.DataFrame([{"season": 2026, "week": 2, "team": "SEA",
                                "def_sacks": 2.0}])
    schedule = pd.DataFrame([{
        "game_id": "2026_02_SEA_LA", "season": 2026, "week": 2,
        "kickoff": pd.Timestamp("2026-09-14 20:15:00"),
        "home_team": "SEA", "away_team": "LA",
        "home_score": None, "away_score": None,
    }])
    assert dst_actuals(team_weeks, schedule).empty


# --- the script: which boards get graded, and which day ----------------------


def _bank(root: Path, stamp: str, name: str, frame: pd.DataFrame) -> None:
    folder = root / f"run-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(folder / f"{name}_{stamp}.parquet", index=False)


def test_only_the_most_recent_played_day_is_graded() -> None:
    grader = _grader()
    now = datetime(2026, 9, 12, 18, 0, tzinfo=UTC)
    stamps = ["20260910T160000Z", "20260911T160000Z", "20260911T213000Z",
              "20260912T160000Z"]
    # Today's boards have not been played; the older day is already banked.
    assert grader.latest_prior_day(stamps, now) == [
        "20260911T160000Z", "20260911T213000Z"]


def test_a_day_with_nothing_prior_grades_nothing() -> None:
    grader = _grader()
    now = datetime(2026, 9, 12, 18, 0, tzinfo=UTC)
    assert grader.latest_prior_day(["20260912T160000Z"], now) == []


def test_every_banked_board_kind_is_found(tmp_path: Path) -> None:
    grader = _grader()
    frame = _lineup(("UTIL", "Ronald Acuna", 12.0))
    for name in ("dfs_lineup_mlb", "dfs_showdown_mlb", "dfs_tiered_mlb"):
        _bank(tmp_path, "20260911T160000Z", name, frame)
    # Another league's boards are not this league's record.
    _bank(tmp_path, "20260911T160000Z", "dfs_lineup_nfl", frame)
    found = grader.entry_paths(tmp_path, "mlb")
    assert set(found) == {"20260911T160000Z"}
    assert len(found["20260911T160000Z"]) == 3


def test_a_single_stat_board_is_refused_rather_than_graded(tmp_path: Path) -> None:
    """Touchdowns are not DK points, and summing them together says nothing."""
    grader = _grader()
    dk = _lineup(("UTIL", "Ronald Acuna", 12.0)).assign(unit="DK pts")
    home_runs = _lineup(("Tier 1", "Matt Olson", 0.3)).assign(unit="HR")
    _bank(tmp_path, "20260911T160000Z", "dfs_lineup_mlb", dk)
    _bank(tmp_path, "20260911T160000Z", "dfs_tiered_mlb", home_runs)
    paths = [p for s in grader.entry_paths(tmp_path, "mlb").values() for p in s]
    entries = grader.gradeable_entries(paths)
    assert list(entries["player_name"]) == ["Ronald Acuna"]


def test_every_league_that_builds_a_board_can_now_be_graded() -> None:
    # College was the hold-out: there was no free college player box score
    # here, so those boards reported ungraded rather than being scored against
    # invented actuals. cfbfastR closed it (velocity/ingest/cfb_players.py).
    grader = _grader()
    assert set(grader.GRADEABLE) == {"mlb", "nfl", "wnba", "ncaaf"}
