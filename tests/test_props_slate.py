"""MLB player-prop wagering — prop lines + model distributions → staked bets.

Covers the prop ingest (Odds API per-event props → canonical PropLines) and the
prop slate builder end-to-end: a beatable board produces staked prop bets tagged
with the player, an unresolved player is skipped and reported, and player-name
resolution is normalized.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from velocity.ingest.theoddsapi import extract_events, normalize_player_props
from velocity.models.game_mlb import MLBGameModel
from velocity.models.props_mlb import BaseballProps
from velocity.models.simulate_baseball import (
    BaseballSimConfig,
    Team,
    batter_from_rates,
    pitcher_from_rates,
    simulate_game,
)
from velocity.store.schema import PropLines
from velocity.wagering.props_slate import (
    build_name_index,
    build_prop_slate,
    mlb_prop_slate,
    resolve_player,
)
from velocity.wagering.slate import SlateConfig

FIXTURES = Path(__file__).parent / "fixtures"
AVG_BAT = {"k": 0.225, "bb": 0.085, "hbp": 0.011, "hr": 0.035, "in_play": 0.644}
AVG_BIP = {"single": 0.222, "double": 0.068, "triple": 0.008, "out_bip": 0.702}
AVG_PIT = {"k": 0.225, "bb": 0.080, "hbp": 0.011, "hr": 0.035, "in_play": 0.649}
HIGH_K_PIT = {"k": 0.32, "bb": 0.06, "hbp": 0.01, "hr": 0.03, "in_play": 0.58}


def _props_json() -> list[dict]:
    return json.loads((FIXTURES / "theoddsapi_mlb_props.json").read_text())


def test_normalize_player_props_validates_and_filters() -> None:
    props = normalize_player_props(_props_json())
    PropLines.validate(props)
    # h2h is dropped; two sides each of Ks, total bases, hits = 6 prop rows.
    assert set(props["market"]) == {"pitcher_strikeouts", "total_bases", "hits"}
    assert set(props["side"]) == {"over", "under"}
    assert len(props) == 6
    kershaw = props[(props["market"] == "pitcher_strikeouts") & (props["side"] == "over")]
    assert kershaw.iloc[0]["player"] == "Clayton Kershaw"
    assert kershaw.iloc[0]["point"] == 4.5


def test_name_index_and_resolution_are_normalized() -> None:
    stats = pd.DataFrame(
        {"player_id": ["477132", "660271"], "player_name": ["Clayton Kershaw", "Shohei Ohtani"]}
    )
    index = build_name_index(stats)
    assert resolve_player("clayton  kershaw", index) == "477132"  # spacing/case tolerant
    assert resolve_player("Nobody At All", index) is None


def _props_for_game() -> BaseballProps:
    # Kershaw (477132) starts for the home side; Ohtani (660271) leads off.
    home_lineup = [batter_from_rates("660271", AVG_BAT, AVG_BIP)]
    home_lineup += [batter_from_rates(f"h{i}", AVG_BAT, AVG_BIP) for i in range(8)]
    home = Team(lineup=home_lineup, pitcher=pitcher_from_rates("477132", HIGH_K_PIT))
    away = Team(
        lineup=[batter_from_rates(f"a{i}", AVG_BAT, AVG_BIP) for i in range(9)],
        pitcher=pitcher_from_rates("away_p", AVG_PIT),
    )
    result = simulate_game(
        home, away, np.random.default_rng(3), BaseballSimConfig(n_sims=2500, starter_outs=18)
    )
    return BaseballProps(result)


def test_build_prop_slate_stakes_bets_and_reports_unresolved() -> None:
    props = _props_for_game()
    prop_lines = normalize_player_props(_props_json())
    stats = pd.DataFrame(
        {"player_id": ["477132", "660271"], "player_name": ["Clayton Kershaw", "Shohei Ohtani"]}
    )
    name_to_id = build_name_index(stats)  # "Unknown Guy" deliberately absent

    log, unresolved = build_prop_slate(
        {"evt-mlb-001": props},
        prop_lines,
        name_to_id,
        SlateConfig(exclude_closing=False, min_edge=0.0),
    )

    bets = list(log)
    assert bets, "beatable prop lines should clear a 0% edge threshold"
    for bet in bets:
        assert bet.player in {"Clayton Kershaw", "Shohei Ohtani"}
        assert bet.market in {"pitcher_strikeouts", "total_bases"}
        assert bet.side in {"over", "under"}
        assert bet.stake > 0.0
    # The player with no model id is skipped and surfaced, not guessed.
    assert [u["player"] for u in unresolved] == ["Unknown Guy"]


def test_mlb_prop_slate_resolves_events_and_prices() -> None:
    """End-to-end: a game board + a prop board → staked prop bets, via the model."""
    lad_lineup = [batter_from_rates("660271", AVG_BAT, AVG_BIP)]
    lad_lineup += [batter_from_rates(f"lad{i}", AVG_BAT, AVG_BIP) for i in range(8)]
    lad = Team(lineup=lad_lineup, pitcher=pitcher_from_rates("477132", HIGH_K_PIT))
    sf = Team(
        lineup=[batter_from_rates(f"sf{i}", AVG_BAT, AVG_BIP) for i in range(9)],
        pitcher=pitcher_from_rates("sf_p", AVG_PIT),
    )
    model = MLBGameModel(
        teams={"LAD": lad, "SF": sf},
        config=BaseballSimConfig(n_sims=2000, starter_outs=18),
        seed=3,
    )
    # The game board (LAD @ SF, id evt-mlb-001) and the prop board share the id.
    events = extract_events(json.loads((FIXTURES / "theoddsapi_mlb.json").read_text()))
    prop_lines = normalize_player_props(_props_json())
    stats = pd.DataFrame(
        {"player_id": ["477132", "660271"], "player_name": ["Clayton Kershaw", "Shohei Ohtani"]}
    )

    log, unresolved = mlb_prop_slate(
        model,
        events,
        prop_lines,
        build_name_index(stats),
        config=SlateConfig(exclude_closing=False, min_edge=0.0),
    )
    assert list(log), "resolved players on a beatable board should produce prop bets"
    assert [u["player"] for u in unresolved] == ["Unknown Guy"]


def test_empty_prop_board_yields_no_bets() -> None:
    props = _props_for_game()
    empty = normalize_player_props([])
    log, unresolved = build_prop_slate({"evt-mlb-001": props}, empty, {})
    assert len(log) == 0
    assert unresolved == []
