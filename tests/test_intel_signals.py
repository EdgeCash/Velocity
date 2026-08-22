"""Signals: exact scores, side alignment, abstentions, and vetoes."""

from __future__ import annotations

import pytest
from velocity.intel.context import GameContext, InjuryOut, TeamContext
from velocity.intel.signals import (
    FormSignal,
    InjurySignal,
    MatchupSignal,
    PropAvailabilitySignal,
    PropMatchupSignal,
    RestSignal,
)
from velocity.wagering.bet_log import Bet

_BASELINE = {"off_epa": 0.0, "def_epa": 0.0, "pass_def": 0.0, "rush_def": 0.0,
             "net_ppg": 0.0, "ppg": 22.0}
_DISPERSION = {"off_epa": 0.10, "def_epa": 0.10, "pass_def": 0.08, "rush_def": 0.08,
               "net_ppg": 7.0, "ppg": 5.0}


def _bet(market: str = "spread", side: str = "home", player: str | None = None) -> Bet:
    return Bet(game_id="g1", market=market, side=side, book="book", price=-110,
               stake=1.0, p_model=0.55, p_fair=0.50, player=player)


def _ctx(away: TeamContext, home: TeamContext, **kwargs: object) -> GameContext:
    return GameContext(
        game_id="g1", season=2025, away=away, home=home,
        baseline=dict(_BASELINE), dispersion=dict(_DISPERSION), **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Matchup
# ---------------------------------------------------------------------------


def test_matchup_team_edge_exact_and_mirrored() -> None:
    home = TeamContext(team="HOM", off_epa=0.02, def_epa=-0.01)
    away = TeamContext(team="AWY", off_epa=-0.02, def_epa=0.03)
    ctx = _ctx(away, home)
    result = MatchupSignal().evaluate(_bet(side="home"), ctx)
    assert result is not None
    # z = ((0.2 − (−0.2)) + (0.3 − (−0.1))) / 2 = 0.4, toward the home side.
    assert result.score == pytest.approx(0.4)
    mirrored = MatchupSignal().evaluate(_bet(side="away"), ctx)
    assert mirrored is not None
    assert mirrored.score == pytest.approx(-0.4)


def test_matchup_total_environment_flips_for_under() -> None:
    hot = TeamContext(team="HOM", off_epa=0.05, def_epa=0.05)
    also_hot = TeamContext(team="AWY", off_epa=0.05, def_epa=0.05)
    ctx = _ctx(also_hot, hot)
    over = MatchupSignal().evaluate(_bet("total", "over"), ctx)
    under = MatchupSignal().evaluate(_bet("total", "under"), ctx)
    assert over is not None and under is not None
    assert over.score == pytest.approx(0.5)
    assert under.score == pytest.approx(-0.5)


def test_matchup_falls_back_to_scoring_form_without_epa() -> None:
    home = TeamContext(team="HOM", ppg=28.0, papg=20.0)  # net +8
    away = TeamContext(team="AWY", ppg=20.0, papg=27.0)  # net −7
    result = MatchupSignal().evaluate(_bet(side="home"), _ctx(away, home))
    assert result is not None
    # (8 − (−7)) / (2·7) ≈ 1.07 → clipped to 1.0.
    assert result.score == pytest.approx(1.0)
    assert "scoring-form" in result.rationale


def test_matchup_abstains_without_any_stats_or_spread() -> None:
    bare = _ctx(TeamContext(team="AWY"), TeamContext(team="HOM"))
    assert MatchupSignal().evaluate(_bet(), bare) is None
    # EPA present but no league spread to scale by → abstain, not half-fire.
    ctx = GameContext(
        game_id="g1", season=2025,
        away=TeamContext(team="AWY", off_epa=0.1, def_epa=0.0),
        home=TeamContext(team="HOM", off_epa=0.0, def_epa=0.0),
    )
    assert MatchupSignal().evaluate(_bet(), ctx) is None


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------


def test_form_rewards_the_team_trending_up() -> None:
    home = TeamContext(team="HOM", ppg=24.0, papg=20.0,
                       recent_ppg=30.0, recent_papg=20.0, streak="W3")
    away = TeamContext(team="AWY", ppg=22.0, papg=22.0,
                       recent_ppg=18.0, recent_papg=24.0)
    result = FormSignal().evaluate(_bet(side="home"), _ctx(away, home))
    assert result is not None
    # trends: home +10 − 4 = +6; away −6 − 0 = −6; (6 − (−6)) / (2·7) ≈ 0.857.
    assert result.score == pytest.approx(12.0 / 14.0)
    assert "(W3)" in result.rationale


def test_form_total_uses_combined_scoring_trend() -> None:
    home = TeamContext(team="HOM", ppg=20.0, papg=20.0,
                       recent_ppg=27.0, recent_papg=27.0)  # totals up 14
    away = TeamContext(team="AWY", ppg=22.0, papg=22.0,
                       recent_ppg=22.0, recent_papg=22.0)  # flat
    over = FormSignal().evaluate(_bet("total", "over"), _ctx(away, home))
    assert over is not None
    # mean trend (14 + 0) / 2 = 7 → 7 / (2·7) = 0.5 toward the over.
    assert over.score == pytest.approx(0.5)
    under = FormSignal().evaluate(_bet("total", "under"), _ctx(away, home))
    assert under is not None and under.score == pytest.approx(-0.5)


def test_form_abstains_without_recent_window() -> None:
    home = TeamContext(team="HOM", ppg=24.0, papg=20.0)
    away = TeamContext(team="AWY", ppg=22.0, papg=22.0)
    assert FormSignal().evaluate(_bet(side="home"), _ctx(away, home)) is None


# ---------------------------------------------------------------------------
# Rest
# ---------------------------------------------------------------------------


def test_rest_gap_scores_and_small_gaps_abstain() -> None:
    home = TeamContext(team="HOM", rest_days=10)
    away = TeamContext(team="AWY", rest_days=6)
    result = RestSignal().evaluate(_bet(side="home"), _ctx(away, home))
    assert result is not None
    assert result.score == pytest.approx(4.0 / 7.0)
    close = _ctx(TeamContext(team="AWY", rest_days=7), TeamContext(team="HOM", rest_days=6))
    assert RestSignal().evaluate(_bet(side="home"), close) is None


def test_rest_ignores_totals_and_openers() -> None:
    home = TeamContext(team="HOM", rest_days=200)
    away = TeamContext(team="AWY", rest_days=190)
    ctx = _ctx(away, home)
    assert RestSignal().evaluate(_bet("total", "over"), ctx) is None
    assert RestSignal().evaluate(_bet(side="home"), ctx) is None  # season openers


# ---------------------------------------------------------------------------
# Injuries
# ---------------------------------------------------------------------------


def test_injury_burden_favors_the_healthier_side() -> None:
    home = TeamContext(team="HOM")
    away = TeamContext(team="AWY", outs=(
        InjuryOut("Bob Jones", "WR", "Out"), InjuryOut("Cy West", "CB", "IR"),
    ))
    result = InjurySignal().evaluate(_bet(side="home"), _ctx(away, home))
    assert result is not None
    # Opponent burden 0.3 + 0.25 = 0.55 → 0.55 / 1.5.
    assert result.score == pytest.approx(0.55 / 1.5)
    assert not result.veto


def test_injury_vetoes_a_side_whose_qb_is_out() -> None:
    home = TeamContext(team="HOM", outs=(InjuryOut("Al Smith", "QB", "Out"),))
    ctx = _ctx(TeamContext(team="AWY"), home)
    result = InjurySignal().evaluate(_bet(side="home"), ctx)
    assert result is not None and result.veto and result.score == -1.0
    assert "Al Smith" in result.rationale
    # The other side of the same game is a plain (positive) signal, not a veto.
    other = InjurySignal().evaluate(_bet(side="away"), ctx)
    assert other is not None and not other.veto
    assert other.score == pytest.approx(0.9 / 1.5)


def test_injury_total_leans_under_on_offensive_outs() -> None:
    away = TeamContext(team="AWY", outs=(InjuryOut("Al Smith", "QB", "Out"),))
    home = TeamContext(team="HOM", outs=(InjuryOut("Cy West", "CB", "IR"),))
    ctx = _ctx(away, home)
    over = InjurySignal().evaluate(_bet("total", "over"), ctx)
    assert over is not None
    # env = defense out (0.25) − offense out (0.9) = −0.65 → −0.65/1.5 on the over.
    assert over.score == pytest.approx(-0.65 / 1.5)
    under = InjurySignal().evaluate(_bet("total", "under"), ctx)
    assert under is not None and under.score == pytest.approx(0.65 / 1.5)


def test_injury_abstains_with_no_reported_outs() -> None:
    ctx = _ctx(TeamContext(team="AWY"), TeamContext(team="HOM"))
    assert InjurySignal().evaluate(_bet(), ctx) is None


# ---------------------------------------------------------------------------
# Props
# ---------------------------------------------------------------------------


def test_prop_availability_vetoes_exact_and_fuzzy_names() -> None:
    home = TeamContext(team="HOM", outs=(InjuryOut("Justin Fields", "QB", "Out"),))
    ctx = _ctx(TeamContext(team="AWY"), home)
    signal = PropAvailabilitySignal()
    exact = signal.evaluate(_bet("pass_yards", "over", player="Justin Fields"), ctx)
    assert exact is not None and exact.veto
    fuzzy = signal.evaluate(_bet("pass_yards", "over", player="J. Fields"), ctx)
    assert fuzzy is not None and fuzzy.veto
    healthy = signal.evaluate(_bet("pass_yards", "over", player="Sam Other"), ctx)
    assert healthy is None


def test_prop_matchup_scores_the_opposing_unit() -> None:
    away = TeamContext(team="AWY", pass_def=0.08, rush_def=-0.08)
    home = TeamContext(team="HOM", pass_def=0.0, rush_def=0.0)
    ctx = _ctx(away, home)
    signal = PropMatchupSignal(player_teams={"Pat Quick": "HOM"})
    over = signal.evaluate(_bet("pass_yards", "over", player="Pat Quick"), ctx)
    assert over is not None
    assert over.score == pytest.approx(1.0)  # 0.08 / 0.08 σ, soft pass D
    under = signal.evaluate(_bet("pass_yards", "under", player="Pat Quick"), ctx)
    assert under is not None and under.score == pytest.approx(-1.0)
    rush = signal.evaluate(_bet("rush_yards", "over", player="Pat Quick"), ctx)
    assert rush is not None and rush.score == pytest.approx(-1.0)  # stingy rush D


def test_prop_matchup_abstains_for_unmapped_players_and_markets() -> None:
    ctx = _ctx(TeamContext(team="AWY", pass_def=0.08), TeamContext(team="HOM"))
    signal = PropMatchupSignal(player_teams={"Pat Quick": "HOM"})
    assert signal.evaluate(_bet("pass_yards", "over", player="Unknown Guy"), ctx) is None
    assert signal.evaluate(_bet("field_goals", "over", player="Pat Quick"), ctx) is None
