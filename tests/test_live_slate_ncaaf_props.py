"""The two league-specific seams the NCAAF prop board rides on.

The prop slate read FantasyPros and nothing else, and FantasyPros has no
college endpoint at all — so the NCAAF filter came back empty, the slate
skipped every run, and the prop board the collector kept buying was banked and
never read (audit finding 6). Closing that needed two decisions to stop being
the NFL's by default: where the projection comes from, and how a board team
name becomes a team the projection knows.

These exercise both directly rather than through a full CLI run, because that
is where the league branch actually is.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location(
    "run_live_slate", REPO / "scripts" / "run_live_slate.py"
)
rls = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(rls)


def _args(**kw: object) -> argparse.Namespace:
    base = {"league": "ncaaf", "fp_projections": None,
            "ncaaf_player_games": "datasets/ncaaf/player_games.parquet"}
    return argparse.Namespace(**{**base, **kw})


def _bank(rows: list[dict]) -> pd.DataFrame:
    base = {"season": 2026, "week": 1, "game_id": "g1", "opponent": "Auburn",
            "attempts": 0.0, "pass_yards": 0.0, "pass_tds": 0.0,
            "interceptions": 0.0, "carries": 0.0, "rush_yards": 0.0,
            "rush_tds": 0.0, "targets": 0.0, "receptions": 0.0,
            "receiving_yards": 0.0, "receiving_tds": 0.0, "dk_points": 0.0}
    return pd.DataFrame([{**base, **row} for row in rows])


# --- where the projection comes from -----------------------------------------


def test_ncaaf_projects_from_the_bank_rather_than_a_provider_it_has_no_rows_in(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "player_games.parquet"
    _bank([{"player_id": "qb1", "player_name": "A QB", "team": "Alabama",
            "position": "QB", "attempts": 30.0, "pass_yards": 300.0}]).to_parquet(path)

    frame = rls._prop_projection_frame(_args(ncaaf_player_games=str(path)))

    assert frame is not None and not frame.empty
    assert set(frame["team"]) == {"Alabama"}
    assert "ncaaf props: 1 active players" in capsys.readouterr().out


def test_a_missing_college_bank_says_so_instead_of_raising(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.parquet"
    assert rls._prop_projection_frame(_args(ncaaf_player_games=str(missing))) is None
    assert "no college player bank" in capsys.readouterr().out


def test_the_nfl_path_still_wants_its_provider_file(
    capsys: pytest.CaptureFixture[str]
) -> None:
    """The league branch must not quietly give the NFL a college source."""
    assert rls._prop_projection_frame(_args(league="nfl")) is None
    assert "--fp-projections not supplied" in capsys.readouterr().out


# --- how a board name becomes a team the projection knows ---------------------


def _events(pairs: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"home_team": h, "away_team": a} for h, a in pairs])


def test_a_college_board_name_resolves_to_the_school_the_bank_keys_by() -> None:
    """The Odds API writes "Georgia Bulldogs"; the bank writes "Georgia"."""
    events = _events([("Georgia Bulldogs", "Alabama Crimson Tide")])
    resolve = rls._prop_team_resolver(_args(), events, {"Georgia", "Alabama"})
    assert resolve("Georgia Bulldogs") == "Georgia"
    assert resolve("Alabama Crimson Tide") == "Alabama"


def test_the_longer_school_wins_so_a_satellite_cannot_land_on_its_flagship() -> None:
    """"Georgia Southern Eagles" must never be priced as Georgia."""
    events = _events([("Georgia Southern Eagles", "Georgia Bulldogs")])
    resolve = rls._prop_team_resolver(
        _args(), events, {"Georgia", "Georgia Southern"})
    assert resolve("Georgia Southern Eagles") == "Georgia Southern"
    assert resolve("Georgia Bulldogs") == "Georgia"


def test_a_board_name_the_bank_does_not_cover_resolves_to_nothing() -> None:
    """Skipped and reported by the caller, never guessed into a nearby school."""
    events = _events([("Some FCS School Wildcats", "Georgia Bulldogs")])
    resolve = rls._prop_team_resolver(_args(), events, {"Georgia"})
    assert resolve("Some FCS School Wildcats") is None


def test_the_nfl_resolver_is_still_the_alias_table() -> None:
    resolve = rls._prop_team_resolver(_args(league="nfl"), _events([]), set())
    assert resolve("Kansas City Chiefs") == "KC"


# --- where the LINES come from, which is where the money is -------------------


def _board(path: Path) -> Path:
    """A banked prop board, in the collector's own columns."""
    pd.DataFrame([{"player": "A QB", "market": "pass_yards", "side": "over",
                   "point": 250.5, "price": -110, "book": "dk",
                   "timestamp": pd.Timestamp("2026-09-16 17:30:00"),
                   "game_id": "g1"}]).to_parquet(path)
    return path


def test_the_freshest_banked_board_inside_the_bar_is_priced_for_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    now = pd.Timestamp("2026-09-16 18:00:00")
    _board(tmp_path / "props_ncaaf_20260916T173000Z.parquet")
    _board(tmp_path / "props_ncaaf_20260915T173000Z.parquet")  # a day older

    path = rls._resolve_prop_lines_path(
        _args(prop_lines_file=None, prop_lines_dir=str(tmp_path),
              board_max_age_min=75), now)

    assert path is not None and "20260916T173000Z" in path.name
    assert "no credits spent" in capsys.readouterr().out


def test_a_board_past_the_bar_is_refused_and_its_age_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pricing against a line that moved six hours ago is worse than not pricing."""
    now = pd.Timestamp("2026-09-16 18:00:00")
    _board(tmp_path / "props_ncaaf_20260916T120000Z.parquet")

    path = rls._resolve_prop_lines_path(
        _args(prop_lines_file=None, prop_lines_dir=str(tmp_path),
              board_max_age_min=75), now)

    assert path is None
    assert "360min old" in capsys.readouterr().out


def test_another_leagues_banked_board_is_not_this_leagues(tmp_path: Path) -> None:
    now = pd.Timestamp("2026-09-16 18:00:00")
    _board(tmp_path / "props_nfl_20260916T173000Z.parquet")
    assert rls._resolve_prop_lines_path(
        _args(prop_lines_file=None, prop_lines_dir=str(tmp_path),
              board_max_age_min=75), now) is None


def test_freshness_is_the_filename_stamp_not_the_mtime(tmp_path: Path) -> None:
    """These arrive by unzipping artifacts, so every mtime is extraction time.

    The game board learned this the hard way: selecting on mtime made the
    oldest download win and reported a two-day-old board as fresh.
    """
    import os

    now = pd.Timestamp("2026-09-16 18:00:00")
    stale = _board(tmp_path / "props_ncaaf_20260914T120000Z.parquet")
    fresh = _board(tmp_path / "props_ncaaf_20260916T173000Z.parquet")
    # The stale board is touched LAST, exactly as a newest-first extraction
    # loop would leave it.
    os.utime(fresh, (1_600_000_000, 1_600_000_000))
    os.utime(stale, (1_800_000_000, 1_800_000_000))

    path = rls._resolve_prop_lines_path(
        _args(prop_lines_file=None, prop_lines_dir=str(tmp_path),
              board_max_age_min=75), now)
    assert path is not None and path.name == fresh.name


def test_the_workflow_gives_ncaaf_a_banked_board_and_never_buys_one() -> None:
    """The whole finding is that we bought this board and never read it.

    Buying it a second time in order to read it would be a poor trade, so the
    slate refuses a live pull for NCAAF outright and the workflow hands it the
    boards the collector already bought.
    """
    workflow = (REPO / ".github" / "workflows" / "live-slate.yml").read_text()
    assert "--prop-lines-dir artifacts/props" in workflow
    # And the directory is populated earlier in the same job, or the flag is
    # a path nobody checked — the Statcast mistake.
    assert workflow.index("mkdir -p artifacts/props") < workflow.index(
        "--prop-lines-dir artifacts/props")

    script = (REPO / "scripts" / "run_live_slate.py").read_text()
    assert 'elif args.league == "ncaaf":' in script
    assert "not pulling live" in script

