"""The projection-time starter map — the fit's detected passer is not the starter.

The QB fit detects "the primary passer in the team's latest training game",
which after a Week-18 rest game is the backup (docs/SYSTEM_REVIEW.md §3.1).
These pin the source that sees the present: FantasyPros' projected depth and
the injuries snapshot.
"""

from __future__ import annotations

import pandas as pd
from velocity.features.starters import (
    describe_changes,
    nflverse_ids,
    normalize_player_name,
    qb_depth_by_team,
    starter_map,
)


def _fp(rows: list[tuple[str, str, str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["team", "player_name", "position", "stat", "value"])


FP = _fp([
    ("KC", "Patrick Mahomes", "QB", "pass_yds", 4400.0),
    ("KC", "Patrick Mahomes", "QB", "pass_tds", 30.0),
    ("KC", "Chris Oladokun", "QB", "pass_yds", 120.0),
    ("KC", "Travis Kelce", "TE", "rec_yds", 800.0),
    ("LAR", "Matthew Stafford", "QB", "pass_yds", 4100.0),
    ("ATL", "Michael Penix Jr.", "QB", "pass_yds", 3900.0),
    ("ATL", "Kirk Cousins", "QB", "pass_yds", 400.0),
    ("NYJ", "Justin Fields", "QB", "pass_yds", 3300.0),
    ("NYJ", "Brady Cook", "QB", "pass_yds", 150.0),
    ("MIA", "Quinn Ewers", "QB", "pass_yds", 500.0),
    ("MIA", "Tua Tagovailoa", "QB", "pass_yds", 3800.0),
])
WEEKS = pd.DataFrame({
    "player_id": ["00-mahomes", "00-oladokun", "00-stafford", "00-penix", "00-cousins",
                  "00-fields", "00-cook", "00-tua"],
    "player_name": ["Patrick Mahomes", "Chris Oladokun", "Matthew Stafford",
                    "Michael Penix", "Kirk Cousins", "Justin Fields", "Brady Cook",
                    "Tua Tagovailoa"],
    "position": ["QB"] * 8,
    "season": [2025] * 8,
})


def test_depth_orders_quarterbacks_by_projected_workload() -> None:
    depth = qb_depth_by_team(FP)
    assert depth["KC"] == ["Patrick Mahomes", "Chris Oladokun"]
    # Ordering is by workload, not by row order.
    assert depth["MIA"] == ["Tua Tagovailoa", "Quinn Ewers"]
    # FantasyPros' Rams code maps onto the nflverse key the ratings use.
    assert "LA" in depth and "LAR" not in depth
    # Non-quarterbacks never enter the depth chart.
    assert all("Kelce" not in n for names in depth.values() for n in names)


def test_names_resolve_across_suffix_and_punctuation_drift() -> None:
    assert normalize_player_name("Michael Penix Jr.") == normalize_player_name("Michael Penix")
    assert normalize_player_name("Joe Milton III") == normalize_player_name("joe milton")
    ids = nflverse_ids(WEEKS)
    assert ids[normalize_player_name("Michael Penix Jr.")] == "00-penix"


def test_the_map_picks_qb1_and_the_fit_no_longer_prices_the_week18_backup() -> None:
    overrides, notes = starter_map(FP, WEEKS)
    assert overrides["KC"] == "00-mahomes"
    assert overrides["NYJ"] == "00-fields"
    assert overrides["ATL"] == "00-penix"
    assert overrides["LA"] == "00-stafford"
    assert notes == []
    detected = {"KC": "00-oladokun", "NYJ": "00-cook", "ATL": "00-penix"}
    lines = describe_changes(overrides, detected, WEEKS)
    assert "KC: Patrick Mahomes (fit had Chris Oladokun)" in lines
    # An unchanged starter is not reported as a change.
    assert not any(line.startswith("ATL") for line in lines)


def test_an_out_qb1_demotes_to_the_next_passer() -> None:
    injuries = pd.DataFrame({
        "player_name": ["Patrick Mahomes", "Tyreek Hill"],
        "team": ["KC", "MIA"],
        "position": ["QB", "WR"],
        "status": ["Out", "Out"],
        "is_out": [True, True],
    })
    overrides, notes = starter_map(FP, WEEKS, injuries)
    assert overrides["KC"] == "00-oladokun"
    assert any(n.startswith("KC: Patrick Mahomes is out") for n in notes)
    # A receiver's status never touches the quarterback map.
    assert overrides["MIA"] == "00-tua"


def test_an_unresolvable_name_keeps_the_fits_own_detection() -> None:
    fp = _fp([("DEN", "Bo Nix", "QB", "pass_yds", 3700.0)])
    overrides, notes = starter_map(fp, WEEKS)
    assert overrides == {}
    assert notes == ["DEN: Bo Nix has no nflverse id — keeping the fit's starter"]


def test_empty_inputs_are_inert() -> None:
    assert starter_map(FP.iloc[0:0], WEEKS) == ({}, [])
    assert starter_map(FP, WEEKS.iloc[0:0])[0] == {}
    assert qb_depth_by_team(pd.DataFrame(columns=FP.columns)) == {}
