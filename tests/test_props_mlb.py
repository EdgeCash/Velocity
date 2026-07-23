"""MLB player props (Phase M5) — priced off the sim's per-player sample arrays.

Pins: distributions integrate to 1, the over/under/push split partitions
probability, a pitcher's strikeout prop tracks the contact profile of the lineup
he faces, scratching a batter reprices the opposing pitcher, and the workload cap
makes pitcher counting props realistic. The prop-line-archive CLV backtest in the
DoD needs committed history + a prop-odds feed, so it is an acceptance step, not
part of this offline gate.
"""

from __future__ import annotations

import numpy as np
import pytest
from velocity.features.baseball import DEFAULT_BAT_PRIOR, DEFAULT_BIP_PRIOR, DEFAULT_PIT_PRIOR
from velocity.models.props_mlb import BaseballProps, substitute
from velocity.models.simulate_baseball import (
    BaseballSimConfig,
    Team,
    batter_from_rates,
    pitcher_from_rates,
    simulate_game,
)

HIGH_K_BAT = {"k": 0.35, "bb": 0.06, "hbp": 0.01, "hr": 0.03, "in_play": 0.55}
LOW_K_BAT = {"k": 0.12, "bb": 0.09, "hbp": 0.01, "hr": 0.03, "in_play": 0.75}
_CAP = BaseballSimConfig(n_sims=2500, starter_outs=18)  # ~six-inning starter


def _team(prefix: str, bat_rates: dict[str, float]) -> Team:
    lineup = [batter_from_rates(f"{prefix}{i}", bat_rates, DEFAULT_BIP_PRIOR) for i in range(9)]
    return Team(lineup=lineup, pitcher=pitcher_from_rates(f"{prefix}_p", DEFAULT_PIT_PRIOR))


def _props(home: Team, away: Team, cfg: BaseballSimConfig = _CAP, seed: int = 1) -> BaseballProps:
    return BaseballProps(simulate_game(home, away, np.random.default_rng(seed), cfg))


def test_distributions_integrate_to_one() -> None:
    props = _props(_team("h", DEFAULT_BAT_PRIOR), _team("a", DEFAULT_BAT_PRIOR))
    for player, stat in [("a0", "total_bases"), ("h_p", "pitcher_strikeouts")]:
        dist = props.distribution(player, stat)
        assert sum(dist.values()) == pytest.approx(1.0)
        assert all(v >= 0 for v in dist)  # non-negative integer support


def test_over_under_push_partition() -> None:
    props = _props(_team("h", DEFAULT_BAT_PRIOR), _team("a", DEFAULT_BAT_PRIOR))
    # Whole-number line: over + under + push = 1.
    o = props.prob_over("a0", "total_bases", 1)
    u = props.prob_under("a0", "total_bases", 1)
    p = props.prob_push("a0", "total_bases", 1)
    assert o + u + p == pytest.approx(1.0)
    assert p > 0.0  # a whole-number line has real push mass
    # Half-point line: no push, so over + under = 1.
    assert props.prob_over("a0", "total_bases", 1.5) + props.prob_under(
        "a0", "total_bases", 1.5
    ) == pytest.approx(1.0)


def test_pitcher_strikeout_prop_tracks_lineup_contact() -> None:
    home = _team("h", DEFAULT_BAT_PRIOR)
    vs_whiffers = _props(home, _team("a", HIGH_K_BAT)).mean("h_p", "pitcher_strikeouts")
    vs_contact = _props(home, _team("a", LOW_K_BAT)).mean("h_p", "pitcher_strikeouts")
    # A high-strikeout lineup hands the pitcher far more Ks than a contact lineup.
    assert vs_whiffers > vs_contact + 1.0


def test_scratching_a_batter_reprices_the_pitcher() -> None:
    home = _team("h", DEFAULT_BAT_PRIOR)
    # Away lineup is average except a lone high-strikeout bat in the leadoff slot.
    away = _team("a", DEFAULT_BAT_PRIOR)
    away = substitute(away, "a0", batter_from_rates("a0", HIGH_K_BAT, DEFAULT_BIP_PRIOR))
    before = _props(home, away).mean("h_p", "pitcher_strikeouts")
    # Scratch the whiffer for a contact hitter — the pitcher should lose strikeouts.
    away2 = substitute(away, "a0", batter_from_rates("sub", LOW_K_BAT, DEFAULT_BIP_PRIOR))
    after = _props(home, away2).mean("h_p", "pitcher_strikeouts")
    assert after < before


def test_workload_cap_makes_pitcher_props_realistic() -> None:
    home, away = _team("h", DEFAULT_BAT_PRIOR), _team("a", DEFAULT_BAT_PRIOR)
    capped = _props(home, away, _CAP)
    assert capped.mean("h_p", "pitcher_outs") == pytest.approx(18.0, abs=0.5)
    assert 4.5 <= capped.mean("h_p", "pitcher_strikeouts") <= 7.5  # ~six innings, ~9 K/9
    # A complete game (no cap) inflates the same prop well past a real start.
    full = _props(home, away, BaseballSimConfig(n_sims=2500))
    assert full.mean("h_p", "pitcher_strikeouts") > capped.mean("h_p", "pitcher_strikeouts") + 2.0


def test_batter_total_bases_is_realistic() -> None:
    props = _props(_team("h", DEFAULT_BAT_PRIOR), _team("a", DEFAULT_BAT_PRIOR))
    assert 1.0 <= props.mean("a0", "total_bases") <= 2.5


def test_unknown_stat_and_player_raise() -> None:
    props = _props(_team("h", DEFAULT_BAT_PRIOR), _team("a", DEFAULT_BAT_PRIOR))
    with pytest.raises(ValueError, match="unknown prop stat"):
        props.mean("a0", "doubles")
    with pytest.raises(KeyError):
        props.mean("nobody", "total_bases")


def test_substitute_missing_player_raises() -> None:
    team = _team("h", DEFAULT_BAT_PRIOR)
    with pytest.raises(KeyError):
        substitute(team, "ghost", batter_from_rates("x", LOW_K_BAT, DEFAULT_BIP_PRIOR))
