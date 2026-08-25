"""Contextual MLB DFS projections — scoring, context terms, degradation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.models.dfs_mlb import (
    DfsMlbModel,
    hitter_dk_points,
    pitcher_dk_points,
)


def test_hitter_scoring_matches_dk_by_hand() -> None:
    # 2-for-4 with a double and a homer, 3 RBI, 2 runs, a walk, a steal:
    # singles 0*3 + double 5 + hr 10 + rbi 6 + runs 4 + bb 2 + sb 5 = 32
    row = pd.DataFrame([{"pa": 5, "h": 2, "double": 1, "triple": 0, "hr": 1,
                         "rbi": 3, "r": 2, "bb": 1, "hbp": 0, "sb": 1}])
    assert hitter_dk_points(row).iloc[0] == pytest.approx(32.0)


def test_hitter_scoring_derives_singles_and_never_goes_negative() -> None:
    # 3 hits, all extra-base → zero singles, not a negative count.
    row = pd.DataFrame([{"h": 3, "double": 2, "triple": 1, "hr": 0,
                         "rbi": 0, "r": 0, "bb": 0, "hbp": 0, "sb": 0}])
    assert hitter_dk_points(row).iloc[0] == pytest.approx(2 * 5 + 8)
    # Missing columns contribute nothing rather than raising.
    assert hitter_dk_points(pd.DataFrame([{"h": 1}])).iloc[0] == pytest.approx(3.0)


def test_pitcher_scoring_matches_dk_by_hand() -> None:
    # 6 IP (18 outs), 8 K, win, 2 ER, 4 hits, 1 BB:
    # 13.5 + 16 + 4 - 4 - 2.4 - 0.6 = 26.5
    row = pd.DataFrame([{"outs": 18.0, "k": 8, "win": 1, "er": 2,
                         "hits_allowed": 4, "bb": 1, "hbp": 0}])
    assert pitcher_dk_points(row).iloc[0] == pytest.approx(26.5)


def _bank(seed: int = 5):
    """A synthetic season: two parks, an ace and a gopher, three bat tiers."""
    rng = np.random.default_rng(seed)
    parks = {"Launch Pad": 1.25, "The Cavern": 0.80}
    tiers = {"star": 1.6, "regular": 1.0, "scrub": 0.5}
    games, bats, sps = [], [], []
    for g in range(300):
        home = list(parks)[g % 2]
        away = list(parks)[(g + 1) % 2]
        gid = f"g{g}"
        games.append({"game_id": gid, "season": 2026, "home_team": home,
                      "away_team": away, "kickoff": pd.Timestamp("2026-04-01")
                      + pd.Timedelta(days=g // 2), "home_score": 4,
                      "away_score": 3})
        for side, pid, quality in (("home", "ace", 0.55), ("away", "gopher", 1.6)):
            sps.append({"game_id": gid, "side": side, "starter_id": pid,
                        "outs": 18.0, "batters_faced": 24,
                        "k": int(rng.poisson(9 * (1 / quality))),
                        "bb": 2, "hbp": 0, "hr": 1,
                        "er": int(rng.poisson(2 * quality)),
                        "hits_allowed": int(rng.poisson(5 * quality)),
                        "win": 1 if quality < 1 else 0})
        for slot, (name, mult) in enumerate(tiers.items(), start=1):
            for side in ("home", "away"):
                # A hitter faces the OTHER side's starter.
                faces = 1.6 if side == "home" else 0.55
                scale = mult * parks[home] * faces
                pa = 5 if slot == 1 else 4
                bats.append({
                    "game_id": gid, "side": side,
                    "team": home if side == "home" else away,
                    "batter_id": f"{name}_{side}", "batter_name": name,
                    "lineup_slot": slot, "started": True, "pa": pa,
                    "h": int(rng.poisson(1.1 * scale)),
                    "double": int(rng.poisson(0.25 * scale)), "triple": 0,
                    "hr": int(rng.poisson(0.15 * scale)),
                    "rbi": int(rng.poisson(0.5 * scale)),
                    "r": int(rng.poisson(0.5 * scale)),
                    "bb": int(rng.poisson(0.35)), "hbp": 0, "sb": 0,
                })
    return pd.DataFrame(bats), pd.DataFrame(sps), pd.DataFrame(games)


def test_fit_recovers_batter_tiers_park_and_slot() -> None:
    bats, sps, games = _bank()
    model = DfsMlbModel.fit(bats, sps, games)
    assert model.league_hitter_rate > 0 and model.league_pitcher_rate != 0
    star = model.batter_rate["star_home"]
    regular = model.batter_rate["regular_home"]
    scrub = model.batter_rate["scrub_home"]
    assert star > regular > scrub  # the ordering survives shrinkage
    assert model.park_factor["Launch Pad"] > model.park_factor["The Cavern"]
    assert all(0.85 <= v <= 1.20 for v in model.park_factor.values())
    assert model.slot_pa[1] > model.slot_pa[2]  # leadoff bats more


def test_opposing_pitcher_moves_a_hitter_the_right_way() -> None:
    bats, sps, games = _bank()
    model = DfsMlbModel.fit(bats, sps, games)
    base = model.project_hitter("regular_home", lineup_slot=2)
    vs_ace = model.project_hitter("regular_home", opposing_starter="ace",
                                  lineup_slot=2)
    vs_gopher = model.project_hitter("regular_home", opposing_starter="gopher",
                                     lineup_slot=2)
    assert vs_ace is not None and vs_gopher is not None and base is not None
    assert vs_gopher > base > vs_ace
    assert all(0.75 <= v <= 1.30 for v in model.pitcher_factor.values())


def test_park_and_slot_move_a_hitter_the_right_way() -> None:
    bats, sps, games = _bank()
    model = DfsMlbModel.fit(bats, sps, games)
    hot = model.project_hitter("star_home", venue="Launch Pad", lineup_slot=1)
    cold = model.project_hitter("star_home", venue="The Cavern", lineup_slot=1)
    assert hot is not None and cold is not None and hot > cold
    # More trips to the plate is more expected production.
    lead = model.project_hitter("star_home", lineup_slot=1)
    ninth = model.project_hitter("star_home", lineup_slot=3)
    assert lead is not None and ninth is not None and lead > ninth


def test_pitcher_context_is_off_by_default_on_evidence() -> None:
    # The backtest said context HURT pitcher projections (0.2656 vs 0.2730
    # flat over 146 slates), so a flat rate is what ships.
    bats, sps, games = _bank()
    model = DfsMlbModel.fit(bats, sps, games)
    flat = model.project_pitcher("ace")
    assert flat is not None
    assert model.project_pitcher("ace", venue="Launch Pad") == flat
    assert model.project_pitcher("ace", opposing_team="Launch Pad") == flat


def test_a_hitters_park_hurts_the_pitcher_when_context_is_enabled() -> None:
    bats, sps, games = _bank()
    model = DfsMlbModel.fit(bats, sps, games)
    hot = model.project_pitcher("ace", venue="Launch Pad", use_context=True)
    cold = model.project_pitcher("ace", venue="The Cavern", use_context=True)
    assert hot is not None and cold is not None
    # The park multiplier inverts for pitchers — it must not boost both sides.
    assert cold > hot


def test_unknown_players_and_context_never_guess() -> None:
    bats, sps, games = _bank()
    model = DfsMlbModel.fit(bats, sps, games)
    assert model.project_hitter("nobody") is None
    assert model.project_pitcher("nobody") is None
    # An unseen pitcher or park is NEUTRAL, not invented.
    base = model.project_hitter("star_home", lineup_slot=1)
    assert model.project_hitter("star_home", opposing_starter="unseen",
                                lineup_slot=1) == base
    assert model.project_hitter("star_home", venue="Unseen Park",
                                lineup_slot=1) == base


def test_empty_bank_fits_without_raising() -> None:
    empty = pd.DataFrame(columns=["game_id", "batter_id", "pa", "side",
                                  "started", "lineup_slot"])
    games = pd.DataFrame(columns=["game_id", "season", "home_team", "away_team"])
    model = DfsMlbModel.fit(empty, pd.DataFrame(), games)
    assert model.batter_rate == {} and model.project_hitter("x") is None
