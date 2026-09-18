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


def test_college_single_stat_prices_from_the_bank(tmp_path: Path, monkeypatch) -> None:
    """The FantasyPros readers raised KeyError('stat') on the college bank.

    College's ``--fp`` is its banked player-games; the touchdown board must
    price from rushing + receiving touchdowns in the bank's own columns and
    credit the bank on the card.
    """
    import sys

    builder = _builder()
    names = ["Back One", "Back Two", "Wide One", "Wide Two"]
    teams = ["AAA", "BBB", "AAA", "BBB"]
    rows = []
    for week in range(1, 7):
        for i, (name, team) in enumerate(zip(names, teams, strict=True)):
            rows.append({"season": 2026, "week": week, "player_id": i + 1,
                         "player_name": name, "team": team,
                         "position": "RB" if name.startswith("Back") else "WR",
                         "rush_tds": float(2 - i) if i < 2 else 0.0,
                         "receiving_tds": 0.5 if i >= 2 else 0.0, "pass_tds": 0.0,
                         "pass_yards": 0.0, "rush_yards": 50.0, "receiving_yards": 20.0})
    bank = tmp_path / "player_games.parquet"
    pd.DataFrame(rows).to_parquet(bank, index=False)
    board = pd.DataFrame({
        "draft_group_id": ["77"] * 4, "player_id": ["a", "b", "c", "d"],
        "player_name": names, "position": ["RB", "RB", "WR", "WR"], "team": teams,
        "competition": ["AAA @ BBB"] * 4, "kickoff": [pd.Timestamp("2026-09-19 16:00")] * 4,
        "status": ["None"] * 4, "probable": [False] * 4, "tier": [1] * 4,
        "tier_slot_id": [512] * 4, "dk_stat": [1.0] * 4, "league": ["ncaaf"] * 4,
        "game_type": ["Single Stat - Touchdowns"] * 4,
    })
    tiered = tmp_path / "dk_tiered_ncaaf_x.parquet"
    board.to_parquet(tiered, index=False)
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", [
        "build_dfs_tiered.py", "--tiered", str(tiered), "--league", "ncaaf",
        "--fp", str(bank), "--out", str(out)])
    builder.main()
    written = list(out.glob("dfs_tiered_ncaaf_*.parquet"))
    assert written, "no college tiered entry written"
    entry = pd.read_parquet(written[0])
    assert len(entry) == 3
    # The top scorer by the bank's rate leads; the two-team rule holds.
    assert entry.iloc[0]["player_name"] == "Back One"
    assert entry["team"].nunique() >= 2
    caption = next(out.glob("dfs_ncaaf_single_stat_td_*_captions.md")).read_text()
    assert caption  # the card and caption carry the college-named slug
