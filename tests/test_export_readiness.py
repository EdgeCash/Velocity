"""Is the board usable, and usable in time?

A run exits 0 when the ENGINE finished. Run #132 exited 0 with no props, no
DFS pool and no weather, and nothing said so. These tests pin the second
claim: that the export states what is missing, and that "missing something
optional" and "missing what a card needs" are different verdicts.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from velocity.export.meta import ExportMeta
from velocity.export.readiness import (
    DEGRADED,
    MISSING,
    NOT_READY,
    OK,
    READINESS_COLUMNS,
    READY,
    STALE,
    assess,
    export_readiness,
    readiness_frame,
    verdict_of,
)

NOW = pd.Timestamp("2026-09-20T22:51:00Z")


def _games(kickoff: str = "2026-09-21T17:00:00Z") -> pd.DataFrame:
    return pd.DataFrame([{"game_id": "g1", "home_team": "Carolina",
                          "away_team": "Atlanta", "kickoff": kickoff}])


def _full(**over: pd.DataFrame | None) -> dict[str, pd.DataFrame | None]:
    one = pd.DataFrame([{"x": 1}])
    frames: dict[str, pd.DataFrame | None] = {
        "games": _games(), "projections": one, "market": one, "plays": one,
        "props": one, "team_totals": one, "dfs_pool": one, "weather": one,
        "record": one,
    }
    frames.update(over)
    return frames


def test_a_complete_board_is_ready() -> None:
    r = assess(_full(), now=NOW, generated_at=NOW)
    assert r.verdict == READY
    assert r.missing == ()
    assert all(s.status == OK for s in r.surfaces)


def test_a_missing_optional_surface_degrades_but_does_not_block() -> None:
    """No props tonight is a fact about the night, not a broken board."""
    r = assess(_full(props=None, dfs_pool=pd.DataFrame()), now=NOW, generated_at=NOW)
    assert r.verdict == DEGRADED
    assert {s.key for s in r.missing} == {"props", "dfs_pool"}
    assert r.missing_required == ()


def test_a_missing_required_surface_is_not_ready() -> None:
    r = assess(_full(market=None), now=NOW, generated_at=NOW)
    assert r.verdict == NOT_READY
    assert [s.key for s in r.missing_required] == ["market"]


def test_run_132_reproduced() -> None:
    """The real run: green job, no props, no DFS pool, no weather."""
    r = assess(
        _full(props=None, dfs_pool=None, weather=None),
        now=NOW, generated_at=NOW,
        details={"props": "board 279 min old (bar 75) — too stale to price"},
    )
    assert r.verdict == DEGRADED
    assert {s.key for s in r.missing} == {"props", "dfs_pool", "weather"}
    props = next(s for s in r.surfaces if s.key == "props")
    assert "279 min old" in props.detail
    assert "missing:" in r.summary_line()


def test_old_numbers_with_time_to_spare_are_degraded_not_blocked() -> None:
    """A board worth redoing is a different instruction from one you cannot use."""
    made = NOW - pd.Timedelta(minutes=300)
    r = assess(_full(), now=NOW, generated_at=made,
               max_age_min=240, kickoff_warn_min=90)
    assert r.verdict == DEGRADED
    assert r.board_age_minutes == pytest.approx(300, abs=1)
    games = next(s for s in r.surfaces if s.key == "games")
    assert games.status == STALE
    assert "300 min old" in games.detail
    # Stale and missing are different complaints and must read differently.
    assert "stale: Board (games)" in r.summary_line()
    assert "missing: Board (games)" not in r.summary_line()
    assert r.missing == ()


def test_old_numbers_close_to_kickoff_are_not_ready() -> None:
    """Inside the window there is no time to replace them — say so loudly.

    A scheduled rerun starts 1h48-3h00 late and the run itself takes 20-30
    minutes, so 40 minutes before kickoff a five-hour-old board is final.
    """
    made = NOW - pd.Timedelta(minutes=300)
    soon = (NOW + pd.Timedelta(minutes=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = assess(_full(games=_games(soon)), now=NOW, generated_at=made,
               max_age_min=240, kickoff_warn_min=90)
    assert r.verdict == NOT_READY
    assert r.minutes_to_kickoff == pytest.approx(40, abs=1)


def test_kickoff_already_passed_reads_as_negative() -> None:
    past = (NOW - pd.Timedelta(minutes=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = assess(_full(games=_games(past)), now=NOW, generated_at=NOW)
    assert r.minutes_to_kickoff == pytest.approx(-35, abs=1)
    assert "35 min ago" in r.summary_line()


def test_no_kickoff_column_is_not_an_error() -> None:
    r = assess(_full(games=pd.DataFrame([{"game_id": "g1"}])), now=NOW, generated_at=NOW)
    assert r.minutes_to_kickoff is None
    assert r.next_kickoff is None
    assert r.verdict == READY


def test_summary_line_leads_with_the_verdict() -> None:
    r = assess(_full(props=None), now=NOW, generated_at=NOW)
    assert r.summary_line().startswith(DEGRADED)


def test_the_frame_keeps_its_contract(tmp_path: Path) -> None:
    meta = ExportMeta("2026-09-20T22:51:00Z", 2026, 3)
    r = assess(_full(props=None), now=NOW, generated_at=NOW)
    frame = readiness_frame(r)
    assert list(frame.columns) == [c for c in READINESS_COLUMNS
                                   if c not in ("generated_at", "season", "week")]
    path = export_readiness(meta, r, out_dir=tmp_path)
    written = pd.read_csv(path)
    assert list(written.columns) == list(READINESS_COLUMNS)
    assert set(written["status"]) == {OK, MISSING}


@pytest.mark.parametrize(("frames", "expected"),
                         [({}, READY), ({"props": None}, DEGRADED),
                          ({"games": None}, NOT_READY)])
def test_verdict_round_trips_through_the_file(
    tmp_path: Path, frames: dict, expected: str
) -> None:
    """The CI gate re-reads rather than recomputes, so the file must carry it."""
    meta = ExportMeta("2026-09-20T22:51:00Z", 2026, 3)
    r = assess(_full(**frames), now=NOW, generated_at=NOW)
    path = export_readiness(meta, r, out_dir=tmp_path)
    assert verdict_of(path) == expected


def test_a_truncated_readiness_file_is_not_ready(tmp_path: Path) -> None:
    """A gate that reads nothing must not conclude everything is fine."""
    empty = tmp_path / "readiness.csv"
    empty.write_text("surface,label\n")
    assert verdict_of(empty) == NOT_READY
