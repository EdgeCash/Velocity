"""Player-prop wagering — prop lines + model distributions → staked bets.

Covers the prop ingest (Odds API per-event props → canonical PropLines) and the
prop slate builder end-to-end: a beatable board produces staked prop bets tagged
with the player, an unresolved player is skipped and reported, and player-name
resolution is normalized. The model side is a tiny sample-backed stub satisfying
:class:`~velocity.wagering.props_slate.PropDistributions` — the pricing engine
is what's under test, not any one sport's simulator.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.ingest.theoddsapi import (
    events_of,
    normalize_player_props,
    unwrap,
)
from velocity.store.schema import PropLines
from velocity.wagering.props_slate import (
    build_name_index,
    build_prop_slate,
    resolve_player,
)
from velocity.wagering.slate import SlateConfig

_UPDATE = "2026-09-10T12:00:00Z"


def _outcomes(player: str, point: float, over: int, under: int) -> list[dict]:
    return [
        {"name": "Over", "description": player, "price": over, "point": point},
        {"name": "Under", "description": player, "price": under, "point": point},
    ]


def _props_json() -> list[dict]:
    """One event's per-event odds payload: two prop markets + a to-be-dropped h2h."""
    return [
        {
            "id": "evt-001",
            "sport_key": "americanfootball_nfl",
            "commence_time": "2026-09-10T00:20:00Z",
            "home_team": "Kansas City Chiefs",
            "away_team": "Buffalo Bills",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "last_update": _UPDATE,
                    "markets": [
                        {
                            "key": "player_pass_yds",
                            "last_update": _UPDATE,
                            "outcomes": _outcomes("Josh Allen", 249.5, 100, -120),
                        },
                        {
                            "key": "player_receptions",
                            "last_update": _UPDATE,
                            "outcomes": _outcomes("Travis Kelce", 6.5, 105, -125)
                            + _outcomes("Unknown Guy", 4.5, 100, -110),
                        },
                        {
                            "key": "h2h",
                            "last_update": _UPDATE,
                            "outcomes": [
                                {"name": "Kansas City Chiefs", "price": -145},
                                {"name": "Buffalo Bills", "price": 125},
                            ],
                        },
                    ],
                }
            ],
        }
    ]


class SampleProps:
    """A ``PropDistributions`` stub backed by per-(player, market) sample arrays."""

    def __init__(self, samples: dict[tuple[str, str], np.ndarray]) -> None:
        self.samples = samples

    def has(self, player_id: str, market: str) -> bool:
        return (player_id, market) in self.samples

    def prob_over(self, player_id: str, market: str, point: float) -> float:
        return float(np.mean(self.samples[(player_id, market)] > point))

    def prob_under(self, player_id: str, market: str, point: float) -> float:
        return float(np.mean(self.samples[(player_id, market)] < point))


def _props_for_game() -> SampleProps:
    # Allen clears 249.5 in 70% of sims; Kelce clears 6.5 in 60%.
    return SampleProps({
        ("qb1", "pass_yards"): np.array([300.0] * 70 + [200.0] * 30),
        ("te1", "receptions"): np.array([8.0] * 60 + [5.0] * 40),
    })


STATS = pd.DataFrame(
    {"player_id": ["qb1", "te1"], "player_name": ["Josh Allen", "Travis Kelce"]}
)


def test_events_of_recovers_a_single_per_event_object() -> None:
    """The per-event ``/events/{id}/odds`` endpoint returns ONE event object, not an
    array. ``unwrap`` reads that as empty (no ``data`` key) — the bug that left the
    prop archive empty; ``events_of`` recovers it, so props normalize from a live
    per-event payload."""
    event = _props_json()[0]  # a single event object, as the per-event endpoint returns
    assert unwrap(event) == []  # the old, broken path — silently no props
    assert events_of(event) == [event]  # the fix
    assert not normalize_player_props(events_of(event)).empty
    # and it still agrees with the other shapes:
    assert events_of(_props_json()) == _props_json()  # a bulk array
    assert events_of({"data": _props_json()}) == _props_json()  # a historical wrapper


def test_normalize_player_props_validates_and_filters() -> None:
    props = normalize_player_props(_props_json())
    PropLines.validate(props)
    # h2h is dropped; two sides each of pass yards + two receptions players = 6 rows.
    assert set(props["market"]) == {"pass_yards", "receptions"}
    assert set(props["side"]) == {"over", "under"}
    assert len(props) == 6
    allen = props[(props["market"] == "pass_yards") & (props["side"] == "over")]
    assert allen.iloc[0]["player"] == "Josh Allen"
    assert allen.iloc[0]["point"] == 249.5


def test_name_index_and_resolution_are_normalized() -> None:
    index = build_name_index(STATS)
    assert resolve_player("josh  allen", index) == "qb1"  # spacing/case tolerant
    assert resolve_player("Nobody At All", index) is None


def test_build_prop_slate_stakes_bets_and_reports_unresolved() -> None:
    props = _props_for_game()
    prop_lines = normalize_player_props(_props_json())
    name_to_id = build_name_index(STATS)  # "Unknown Guy" deliberately absent

    log, unresolved = build_prop_slate(
        {"evt-001": props},
        prop_lines,
        name_to_id,
        SlateConfig(exclude_closing=False, min_edge=0.0),
    )

    bets = list(log)
    assert bets, "beatable prop lines should clear a 0% edge threshold"
    for bet in bets:
        assert bet.player in {"Josh Allen", "Travis Kelce"}
        assert bet.market in {"pass_yards", "receptions"}
        assert bet.side in {"over", "under"}
        assert bet.stake > 0.0
    # The player with no model id is skipped and surfaced, not guessed.
    assert [u["player"] for u in unresolved] == ["Unknown Guy"]


def test_resolved_but_unsimulated_player_is_skipped_not_crashed() -> None:
    """A prop board offers lines for more players than the sim put on the field.
    One that resolves to a real id but was never simulated must be skipped, not
    raise KeyError."""
    props = _props_for_game()
    prop_lines = normalize_player_props(_props_json())
    # "Unknown Guy" now resolves — to a real id the sim never put on the field.
    stats = pd.concat(
        [STATS, pd.DataFrame({"player_id": ["wr9"], "player_name": ["Unknown Guy"]})],
        ignore_index=True,
    )
    log, unresolved = build_prop_slate(
        {"evt-001": props},
        prop_lines,
        build_name_index(stats),
        SlateConfig(exclude_closing=False, min_edge=0.0),
    )
    # No crash; the unsimulated player produced no bet and isn't a name-resolution miss.
    assert all(bet.player != "Unknown Guy" for bet in log)
    assert unresolved == []  # resolved — the skip is a pricing gap, not a name miss


def test_prob_shrink_pulls_prop_confidence_toward_half() -> None:
    """SlateConfig.prob_shrink shrinks the model prob toward 0.5 in the prop pricer,
    just as it does on the game slate. 1.0 is untouched; a lower value is less
    confident on every bet that survives."""
    props = _props_for_game()
    prop_lines = normalize_player_props(_props_json())
    name_to_id = build_name_index(STATS)

    raw, _ = build_prop_slate(
        {"evt-001": props}, prop_lines, name_to_id,
        SlateConfig(exclude_closing=False, min_edge=0.0, prob_shrink=1.0),
    )
    shrunk, _ = build_prop_slate(
        {"evt-001": props}, prop_lines, name_to_id,
        SlateConfig(exclude_closing=False, min_edge=0.0, prob_shrink=0.5),
    )
    raw_by_key = {(b.market, b.player, b.side): b.p_model for b in raw}
    matched = 0
    for bet in shrunk:
        key = (bet.market, bet.player, bet.side)
        if key in raw_by_key:  # p → 0.5 + 0.5·(p−0.5)
            assert bet.p_model == pytest.approx(0.5 + 0.5 * (raw_by_key[key] - 0.5))
            matched += 1
    assert matched  # the shrink path actually ran on real bets


def test_exclude_markets_skips_a_no_edge_market() -> None:
    """A market in SlateConfig.exclude_markets is never staked, while the others
    price normally."""
    props = _props_for_game()
    prop_lines = normalize_player_props(_props_json())
    name_to_id = build_name_index(STATS)

    kept, _ = build_prop_slate(
        {"evt-001": props}, prop_lines, name_to_id,
        SlateConfig(exclude_closing=False, min_edge=0.0),
    )
    excluded, _ = build_prop_slate(
        {"evt-001": props}, prop_lines, name_to_id,
        SlateConfig(
            exclude_closing=False, min_edge=0.0, exclude_markets=frozenset({"receptions"})
        ),
    )
    assert any(b.market == "receptions" for b in kept)  # normally staked
    assert all(b.market != "receptions" for b in excluded)  # excluded → never staked
    # the other markets are unaffected
    other = {b.market for b in kept if b.market != "receptions"}
    assert other and other == {b.market for b in excluded}


def test_empty_prop_board_yields_no_bets() -> None:
    props = _props_for_game()
    empty = normalize_player_props([])
    log, unresolved = build_prop_slate({"evt-001": props}, empty, {})
    assert len(log) == 0
    assert unresolved == []
