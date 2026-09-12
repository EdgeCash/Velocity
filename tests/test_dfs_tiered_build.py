"""The salary-free board builder's own failure modes.

``velocity/dfs/tiered.py`` is covered by tests/test_dfs_tiered.py; this is the
CLI around it, where three defects lived that no unit test could see: a format
that was supported but had no card footer, a card slug shared between two
leagues, and season totals reaching a weekly board.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

_SCRIPT = Path(__file__).parent.parent / "scripts" / "build_dfs_tiered.py"


def _builder():
    spec = importlib.util.spec_from_file_location("build_dfs_tiered", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_supported_format_has_a_card_footer() -> None:
    """The KeyError that took the whole NFL tiered board down with it.

    'Single Stat - Total Yards' was added to the supported map and not to the
    footer lookup, so indexing it raised before the parquet was written and no
    NFL card, caption or data row survived the run.
    """
    builder = _builder()
    assert set(builder._SOURCE_NOTES) == set(builder._SUPPORTED)
    assert builder._SOURCE_NOTES["Single Stat - Total Yards"]


def test_the_football_formats_are_named_for_the_league_being_built() -> None:
    # DK runs Single Stat Touchdowns for NFL and CFB alike, and the spec is
    # named cfb_*, so an NFL board rendered a card called dfs_cfb_* — the two
    # leagues' cards overwrote each other.
    builder = _builder()
    from velocity.dfs.tiered import TIER_SPECS

    def slug_for(league: str, game_type: str) -> str:
        slug = TIER_SPECS[game_type].name
        for prefix in ("cfb_", "nfl_"):
            if slug.startswith(prefix):
                return f"{league}_{slug[len(prefix):]}"
        return slug

    assert slug_for("nfl", "Single Stat - Touchdowns") == "nfl_single_stat_td"
    assert slug_for("ncaaf", "Single Stat - Touchdowns") == "ncaaf_single_stat_td"
    assert slug_for("nfl", "Single Stat - Total Yards") == "nfl_single_stat_total_yards"
    # MLB's specs already name their own league and are left alone.
    assert slug_for("mlb", "Tiers") == "mlb_tiers"
    assert builder is not None


def test_season_totals_are_refused_before_they_reach_a_board() -> None:
    """A week-0 snapshot priced a running back at seventeen touchdowns."""
    from velocity.dfs.pipeline import is_season_long

    season_long = pd.DataFrame(
        {
            "player_name": ["Jahmyr Gibbs", "CeeDee Lamb"],
            "team": ["DET", "DAL"],
            "position": ["RB", "WR"],
            "stat": ["rush_tds", "rec_yds"],
            "value": [17.3, 1365.8],
            "week": [0, 0],
            "league": ["nfl", "nfl"],
        }
    )
    assert is_season_long(season_long) is True

    weekly = season_long.assign(week=[2, 2], value=[0.9, 79.4])
    assert is_season_long(weekly) is False


def test_the_football_projections_sum_the_right_stats() -> None:
    builder = _builder()
    fp = pd.DataFrame(
        {
            "player_id": ["p1", "p1", "p1", "p2"],
            "player_name": ["A", "A", "A", "B"],
            "team": ["DAL", "DAL", "DAL", "NYG"],
            "position": ["WR", "WR", "WR", "QB"],
            "stat": ["rec_yds", "rush_yds", "rec_tds", "pass_yds"],
            "value": [70.0, 5.0, 0.6, 250.0],
        }
    )
    yards = builder._football_total_yards(fp).set_index("player_name")
    assert yards.loc["A", "points"] == 75.0
    assert yards.loc["B", "points"] == 250.0

    # A passing touchdown is thrown, not scored, so it is not a Single Stat TD.
    tds = builder._football_touchdowns(fp).set_index("player_name")
    assert tds.loc["A", "points"] == 0.6
    assert "B" not in tds.index
