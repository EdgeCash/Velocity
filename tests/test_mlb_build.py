"""Live MLB model assembly (StatsAPI lineups + rates → MLBGameModel).

Pure, offline coverage of the last-mile wiring: parsing a probable-lineups
payload, projecting season stats into player pools, and stitching them into a
model keyed by rating code — with league-average fallbacks for missing players,
starters, or lineups. The network fetch (`build_live_mlb_model`) is out of the
gate, as with every other adapter.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from velocity.ingest.mlb import normalize_lineups, normalize_player_stats
from velocity.models.mlb_build import assemble_model, build_player_pools

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_normalize_lineups_parses_starters_and_orders() -> None:
    games = normalize_lineups(_load("mlb_lineups.json"))
    assert len(games) == 1
    g = games[0]
    assert (g.home_team, g.away_team) == ("Los Angeles Dodgers", "San Francisco Giants")
    assert g.home_pitcher_id == "477132"  # Kershaw
    assert g.home_lineup[0] == "660271" and len(g.home_lineup) == 9
    assert len(g.away_lineup) == 9


def test_build_player_pools_projects_rates() -> None:
    batters, pitchers = build_player_pools(
        normalize_player_stats(_load("mlb_hitting.json"), "bat"),
        normalize_player_stats(_load("mlb_pitching.json"), "pit"),
    )
    assert "660271" in batters and "477132" in pitchers  # Ohtani, Kershaw
    # A projected rate vector is a valid distribution.
    assert np.isclose(batters["660271"].pa.sum(), 1.0)
    assert np.isclose(batters["660271"].bip.sum(), 1.0)
    assert np.isclose(pitchers["477132"].pa.sum(), 1.0)
    # Kershaw's projected K rate sits above league average (he's a high-K arm).
    assert pitchers["477132"].pa[0] > 0.25


def test_assemble_model_keys_by_code_with_fallbacks() -> None:
    lineups = normalize_lineups(_load("mlb_lineups.json"))
    batters, pitchers = build_player_pools(
        normalize_player_stats(_load("mlb_hitting.json"), "bat"),
        normalize_player_stats(_load("mlb_pitching.json"), "pit"),
    )
    model, unresolved = assemble_model(lineups, batters, pitchers)
    assert not unresolved
    assert set(model.known_teams) == {"LAD", "SF"}

    # The home starter (Kershaw, in the pool) is used verbatim; the away starter
    # (not in the pool) falls back to a league-average arm.
    lad = model.teams["LAD"]
    assert lad.pitcher.player_id == "477132"
    assert lad.pitcher.pa[0] > 0.25  # real high-K rate, not the ~0.225 fallback
    sf = model.teams["SF"]
    assert sf.pitcher.player_id == "700001"
    assert np.isclose(sf.pitcher.pa[0], 0.225)  # fallback league rate

    # Ohtani (in the pool) leads off for LAD; unknown ids padded to a full nine.
    assert lad.lineup[0].player_id == "660271"
    assert len(lad.lineup) == 9
    assert model.project_full("LAD", "SF").p_home_win() > 0.0


def test_unresolved_team_is_reported() -> None:
    from velocity.ingest.mlb import GameLineups

    bogus = GameLineups(
        game_id="1",
        home_team="Los Angeles Dodgers",
        away_team="Sioux Falls Canaries",  # not an MLB club
        home_pitcher_id=None,
        away_pitcher_id=None,
        home_lineup=(),
        away_lineup=(),
    )
    model, unresolved = assemble_model([bogus], {}, {})
    assert unresolved == ["Sioux Falls Canaries"]
    assert set(model.known_teams) == {"LAD"}  # the resolved side still builds
