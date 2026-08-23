"""Deep Dive card — form math, rank/advantage logic, sim probabilities, render."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import GameSim
from velocity.report.deepdive import (
    build_deep_dives,
    build_rows,
    deep_dive_caption,
    epa_form,
    scoring_form,
)
from velocity.report.social import MarketView, SocialCard

RNG = np.random.default_rng(11)


def _games() -> pd.DataFrame:
    rows = [
        # season, week, home, away, home_score, away_score
        (2025, 1, "KC", "BUF", 27, 24),
        (2025, 2, "BUF", "NYJ", 31, 10),
        (2025, 2, "KC", "NYJ", 20, 23),
        (2025, 3, "BUF", "KC", 20, 20),  # a tie
        (2024, 18, "KC", "BUF", 40, 3),  # old season: excluded from form
        (2025, 20, "DEN", "LV", None, None),  # unplayed: excluded
    ]
    return pd.DataFrame(rows, columns=["season", "week", "home_team", "away_team",
                                       "home_score", "away_score"])


def _plays() -> pd.DataFrame:
    rows = []
    # BUF offense strong on passes (+0.3), KC defense leaky (+0.2 allowed).
    for epa, pos, deft, kind in [
        (0.3, "BUF", "KC", "pass"), (0.3, "BUF", "KC", "pass"),
        (0.0, "BUF", "KC", "run"),
        (-0.1, "KC", "BUF", "pass"), (0.1, "KC", "BUF", "run"),
        (0.0, "NYJ", "BUF", "pass"), (-0.2, "NYJ", "KC", "run"),
        (9.9, "BUF", "KC", "punt"),  # non-scrimmage: excluded
    ]:
        rows.append({"season": 2025, "week": 1, "posteam": pos, "defteam": deft,
                     "play_type": kind, "epa": epa})
    return pd.DataFrame(rows)


def test_scoring_form_uses_newest_completed_season() -> None:
    season, form = scoring_form(_games())
    assert season == 2025
    kc = form.loc["KC"]
    # KC 2025: W 27-24, L 20-23, T 20-20 → 1-1-1, ppg (27+20+20)/3
    assert (int(kc["wins"]), int(kc["losses"]), int(kc["ties"])) == (1, 1, 1)
    assert kc["ppg"] == pytest.approx((27 + 20 + 20) / 3)
    assert kc["papg"] == pytest.approx((24 + 23 + 20) / 3)
    assert "LV" not in form.index  # unplayed games contribute nothing


def test_epa_form_splits_units_and_drops_special_teams() -> None:
    form = epa_form(_plays(), 2025)
    assert form.loc["BUF", "pass_off"] == pytest.approx(0.3)
    assert form.loc["BUF", "off_epa"] == pytest.approx((0.3 + 0.3 + 0.0) / 3)
    # KC's defense faced BUF's three plays and NYJ's -0.2 run.
    assert form.loc["KC", "def_epa"] == pytest.approx((0.3 + 0.3 + 0.0 - 0.2) / 4)
    # The 9.9-EPA punt never contaminates the averages.
    assert form["off_epa"].max() < 1.0


def test_build_rows_marks_only_clear_advantages() -> None:
    scoring = pd.DataFrame({
        "ppg": {"BUF": 30.0, "KC": 20.0, "NYJ": 21.0, "DEN": 22.0},
        "papg": {"BUF": 20.0, "KC": 20.4, "NYJ": 28.0, "DEN": 27.0},
    })
    rows = build_rows("BUF", "KC", scoring, None)
    ppg = next(r for r in rows if r.label == "POINTS / GM")
    assert ppg.advantage == "away"  # 30 vs 20 clears any threshold
    assert (ppg.away_rank, ppg.home_rank) == (1, 4)
    papg = next(r for r in rows if r.label == "POINTS ALLOWED / GM")
    assert papg.advantage is None  # 20.0 vs 20.4: a coin flip stays unmarked
    assert papg.away_rank == 1  # lower allowed = better rank


def _projection(mu_home: float = 24.0, mu_away: float = 21.0) -> GameProjection:
    n = 4000
    home = np.maximum(RNG.normal(mu_home, 10.0, n).round(), 0.0)
    away = np.maximum(RNG.normal(mu_away, 10.0, n).round(), 0.0)
    return GameProjection("KC", "BUF", mu_home, mu_away, GameSim(home, away))


def _card(**overrides: object) -> SocialCard:
    base: dict = {
        "game_id": "g1", "away_name": "Buffalo Bills",
        "home_name": "Kansas City Chiefs", "away_code": "BUF", "home_code": "KC",
        "kickoff": pd.Timestamp("2026-09-11 00:20"), "p_home_win": 0.58,
        "mu_away": 21.0, "mu_home": 24.0, "fair_spread": -3.0,
        "fair_total": 45.0, "total_points_pmf": {45: 1.0}, "n_sims": 4000,
        "market_view": MarketView(spread_home=-6.5, total=47.5),
    }
    base.update(overrides)
    return SocialCard(**base)


def test_build_deep_dives_computes_cover_and_over() -> None:
    proj = _projection()
    dives = build_deep_dives([_card()], {"g1": proj}, _games(), _plays())
    assert len(dives) == 1
    dive = dives[0]
    margin = proj.sim.margin
    assert dive.p_home_cover == pytest.approx(float(np.mean(margin > 6.5)))
    assert dive.p_over == pytest.approx(float(np.mean(proj.sim.total > 47.5)))
    assert sum(dive.margin_pmf.values()) == pytest.approx(1.0)
    assert dive.stat_season == 2025
    assert dive.away_record and dive.home_record
    assert len(dive.rows) == 10  # scoring + last-10 recency + six EPA unit rows
    assert any(r.label == "POINTS / GM — LAST 10" for r in dive.rows)
    text = deep_dive_caption(dive)
    assert "covers in" in text and "sims" in text


def test_build_deep_dives_maps_display_codes_to_dataset_keys() -> None:
    card = _card(away_code="BUFF", home_code="KAN")
    dives = build_deep_dives(
        [card], {"g1": _projection()}, _games(), None,
        team_names={"BUFF": "BUF", "KAN": "KC"},
    )
    assert dives[0].away_record != ""  # resolved through the mapping
    # Scoring + last-10 recency rows only without plays (no EPA table).
    assert len(dives[0].rows) == 4


def test_render_deep_dive_writes_a_png(tmp_path: Path) -> None:
    from velocity.report.deepdive_png import render_deep_dives

    dives = build_deep_dives([_card()], {"g1": _projection()}, _games(), _plays())
    paths = render_deep_dives(dives, tmp_path, "20260910T120000Z", league="nfl")
    assert paths[0].name == "deepdive_nfl_20260910T120000Z_BUF_at_KC.png"
    assert paths[0].stat().st_size > 20_000
    captions = tmp_path / "deepdive_nfl_20260910T120000Z_captions.md"
    assert "BUF @ KC" in captions.read_text()


def test_team_form_last5_streak_and_rest() -> None:
    from velocity.report.deepdive import team_form

    games = _games().copy()
    games["kickoff"] = pd.to_datetime("2025-09-01") + pd.to_timedelta(
        games["week"] * 7, unit="D"
    )
    form = team_form(games, "KC", 2025, kickoff=pd.Timestamp("2025-09-24"))
    # KC 2025: W (vs BUF), L (vs NYJ), T (at BUF) — newest last.
    assert form["last5"] == ("W", "L", "T")
    assert form["streak"] == "T1"
    # Last game week 3 → Sep 22; kickoff Sep 24 → 2 days rest.
    assert form["rest"] == 2
    buf = team_form(games, "BUF", 2025)
    assert buf["last5"] == ("L", "W", "T") and buf["rest"] is None
    empty = team_form(games, "NOPE", 2025)
    assert empty["last5"] == () and empty["streak"] == ""


def test_probable_line_formats_the_banked_stats() -> None:
    from velocity.report.deepdive import probable_line

    starters = pd.DataFrame([
        {"game_id": "a", "starter_id": "99", "starter_name": "Paul Skenes",
         "outs": 18.0, "k": 8},
        {"game_id": "b", "starter_id": "99", "starter_name": "Paul Skenes",
         "outs": 20.0, "k": 9},
        {"game_id": "c", "starter_id": "77", "starter_name": "No Outs Guy",
         "outs": 0.0, "k": 0},
    ])
    line = probable_line(starters, "99")
    # 38 outs = 12.2 IP; 17 K over 38 outs = 12.08 K/9.
    assert line == "SKENES · 12.2 IP · 12.1 K/9"
    assert probable_line(starters, "77") is None  # no banked innings
    assert probable_line(starters, None) is None
    assert probable_line(None, "99") is None


def test_build_deep_dives_carries_form_and_probables(tmp_path: Path) -> None:
    games = _games().copy()
    games["kickoff"] = pd.to_datetime("2025-09-01") + pd.to_timedelta(
        games["week"] * 7, unit="D"
    )
    games["game_id"] = [f"g{i}" for i in range(len(games))]
    starters = pd.DataFrame([
        # g0 is a 2025 game; g4 is the 2024 game — the old season's start
        # must NOT count toward the probable's current-season line.
        {"game_id": "g0", "starter_id": "5", "starter_name": "Home Ace",
         "outs": 27.0, "k": 12},
        {"game_id": "g4", "starter_id": "5", "starter_name": "Home Ace",
         "outs": 27.0, "k": 3},
    ])
    probables = {("KC", "BUF", None): ("5", None)}  # (home SP, away SP)
    dives = build_deep_dives(
        [_card(kickoff=pd.Timestamp("2025-09-24"))], {"g1": _projection()},
        games, None, starters=starters, probables=probables,
    )
    dive = dives[0]
    assert dive.away_last5 and dive.home_last5
    assert dive.home_sp == "ACE · 9.0 IP · 12.0 K/9"
    assert dive.away_sp is None  # unannounced probable stays honest
    assert dive.away_rest is not None and dive.home_rest is not None
    # And the renderer accepts the new fields end-to-end.
    from velocity.report.deepdive_png import render_deep_dives

    paths = render_deep_dives(dives, tmp_path, "20250924T120000Z", league="mlb")
    assert paths[0].stat().st_size > 20_000


# ---------------------------------------------------------------------------
# The verdict band: plays, tiers, and the model's why snippet.
# ---------------------------------------------------------------------------


def test_playcall_labels_read_like_a_bettor_writes_them() -> None:
    from velocity.report.deepdive import PlayCall

    spread = PlayCall("spread", "home", -3.5, -110, "bookA", 2.1, tier="A")
    assert spread.label("BUF", "KC") == "KC -3.5 · -110 (bookA) · 2.1u · tier A"
    ml = PlayCall("moneyline", "away", None, 140, "dk", 1.0)
    assert ml.label("BUF", "KC") == "BUF ML · +140 (dk) · 1.0u"
    total = PlayCall("total", "over", 47.5, -108, "fd", 3.0)
    assert total.label("BUF", "KC").startswith("OVER 47.5 · -108")
    tt = PlayCall("team_total_away", "under", 21.5, -105, "dk", 1.5)
    assert tt.label("BUF", "KC").startswith("BUF TT UNDER 21.5")


def test_plays_from_bets_skips_props_and_maps_tiers() -> None:
    from velocity.report.deepdive import plays_from_bets
    from velocity.wagering.bet_log import Bet

    bets = [
        Bet(game_id="g1", market="spread", side="home", book="bookA", price=-110,
            stake=2.1, p_model=0.57, p_fair=0.50, point=-3.5),
        Bet(game_id="g1", market="pass_yards", side="over", book="dk", price=-110,
            stake=1.0, p_model=0.6, point=249.5, player="Josh Allen"),
        Bet(game_id="g2", market="total", side="under", book="fd", price=-105,
            stake=1.0, p_model=0.55, point=44.5),
    ]
    plays = plays_from_bets(bets, tiers={("g1", "spread", "home"): "A"})
    assert set(plays) == {"g1", "g2"}
    assert len(plays["g1"]) == 1  # the prop stayed out
    call = plays["g1"][0]
    assert call.tier == "A"
    assert call.edge == pytest.approx(0.07)
    assert plays["g2"][0].tier is None


def test_model_why_states_projection_market_and_evidence() -> None:
    from velocity.report.deepdive import model_why

    proj = _projection()
    dives = build_deep_dives([_card()], {"g1": proj}, _games(), _plays())
    dive = dives[0]
    why = model_why(
        dive.card, dive.rows, dive.p_home_cover, dive.p_over,
        signals=("unit EPA edge +0.5σ toward BUF",), n_sims=4000,
    )
    assert "Model projects BUF 21.0–KC 24.0" in why
    assert "fair line KC -3.0" in why
    assert "vs the market's KC -6.5 / O/U 47.5" in why
    assert "covers in" in why and "4,000 sims" in why
    assert "Unit EPA edge +0.5 sd toward BUF." in why  # capitalized, σ → sd for the card face


def test_build_deep_dives_attaches_plays_and_why() -> None:
    from velocity.report.deepdive import PlayCall

    call = PlayCall("spread", "home", -6.5, -110, "bookA", 2.0, tier="B")
    dives = build_deep_dives(
        [_card()], {"g1": _projection()}, _games(), _plays(),
        plays_by_game={"g1": (call,)},
        why_signals={"g1": ("rest edge: KC 10d vs BUF 6d",)},
    )
    dive = dives[0]
    assert dive.plays == (call,)
    assert "Rest edge: KC 10d vs BUF 6d." in dive.why
    caption = deep_dive_caption(dive)
    assert "The play: KC -6.5 · -110 (bookA) · 2.0u · tier B." in caption
    assert "Why: Model projects" in caption


def test_caption_renders_a_pass_verdict_when_no_plays() -> None:
    dives = build_deep_dives([_card()], {"g1": _projection()}, _games(), _plays())
    caption = deep_dive_caption(dives[0])
    assert "No edge at today's numbers — pass." in caption


def test_render_verdict_band_writes_a_png(tmp_path: Path) -> None:
    from velocity.report.deepdive import PlayCall
    from velocity.report.deepdive_png import render_deep_dives

    call = PlayCall("total", "over", 47.5, -108, "bookA", 2.5, tier="A")
    dives = build_deep_dives(
        [_card()], {"g1": _projection()}, _games(), _plays(),
        plays_by_game={"g1": (call,)},
    )
    paths = render_deep_dives(dives, tmp_path, "20260910T120000Z", league="nfl")
    assert paths and paths[0].exists() and paths[0].stat().st_size > 10_000
    captions = tmp_path / "deepdive_nfl_20260910T120000Z_captions.md"
    assert "The play: OVER 47.5" in captions.read_text()


def _mlb_games() -> pd.DataFrame:
    rows = [
        ("m1", 2026, 1, "New York Yankees", "Boston Red Sox", 5, 3),
        ("m2", 2026, 2, "Boston Red Sox", "New York Yankees", 2, 6),
        ("m3", 2026, 3, "New York Yankees", "Baltimore Orioles", 4, 4),
    ]
    return pd.DataFrame(rows, columns=["game_id", "season", "week", "home_team",
                                       "away_team", "home_score", "away_score"])


def test_recent_scoring_form_windows_the_newest_games() -> None:
    from velocity.report.deepdive import recent_scoring_form

    rows = [(2026, w, "A", "B", 10 + w, 0) for w in range(1, 13)]
    games = pd.DataFrame(rows, columns=["season", "week", "home_team",
                                        "away_team", "home_score", "away_score"])
    recent = recent_scoring_form(games, 2026, n=10)
    # A's last 10 home scores are weeks 3..12 → mean of 13..22.
    assert recent.loc["A", "ppg"] == pytest.approx(sum(range(13, 23)) / 10)
    assert recent.loc["B", "papg"] == pytest.approx(sum(range(13, 23)) / 10)


def test_rotation_form_rates_and_lineup_ks() -> None:
    from velocity.report.deepdive import rotation_form

    starters = pd.DataFrame([
        # NYY starter: 18 outs (6 IP), 8 K, 1 BB, 1 HR — faced BOS's lineup.
        {"game_id": "m1", "team": "New York Yankees", "side": "home",
         "outs": 18.0, "k": 8, "bb": 1, "hr": 1},
        # BOS starter: 15 outs (5 IP), 3 K, 4 BB, 2 HR — faced NYY's lineup.
        {"game_id": "m1", "team": "Boston Red Sox", "side": "away",
         "outs": 15.0, "k": 3, "bb": 4, "hr": 2},
    ])
    form = rotation_form(starters, _mlb_games())
    nyy = form.loc["New York Yankees"]
    assert nyy["sp_k9"] == pytest.approx(8 / 6 * 9)
    assert nyy["sp_ip"] == pytest.approx(6.0)
    # NYY's lineup struck out 3 times against the opposing starter.
    assert nyy["bat_k_pg"] == pytest.approx(3.0)
    bos = form.loc["Boston Red Sox"]
    assert bos["sp_bb9"] == pytest.approx(4 / 5 * 9)
    assert bos["bat_k_pg"] == pytest.approx(8.0)


def test_build_rows_mlb_wall_uses_runs_noun_and_rotation() -> None:
    scoring = pd.DataFrame({
        "ppg": {"New York Yankees": 5.2, "Boston Red Sox": 4.1},
        "papg": {"New York Yankees": 3.9, "Boston Red Sox": 4.8},
    })
    rotation = pd.DataFrame({
        "sp_k9": {"New York Yankees": 9.5, "Boston Red Sox": 7.2},
        "sp_bb9": {"New York Yankees": 2.1, "Boston Red Sox": 3.8},
        "sp_hr9": {"New York Yankees": 0.9, "Boston Red Sox": 1.4},
        "sp_ip": {"New York Yankees": 5.8, "Boston Red Sox": 5.1},
        "bat_k_pg": {"New York Yankees": 7.5, "Boston Red Sox": 9.8},
    })
    rows = build_rows("Boston Red Sox", "New York Yankees", scoring, None,
                      rotation=rotation, league="mlb")
    labels = [r.label for r in rows]
    assert labels[0] == "RUNS / GM"
    assert "SP K / 9" in labels and "LINEUP K / GM vs SP" in labels
    k9 = next(r for r in rows if r.label == "SP K / 9")
    assert k9.advantage == "home"  # 9.5 vs 7.2, higher is better
    bb9 = next(r for r in rows if r.label == "SP BB / 9")
    assert bb9.advantage == "home"  # 2.1 vs 3.8, lower is better


def test_build_deep_dives_mlb_carries_the_stat_wall() -> None:
    card = _card(away_name="Boston Red Sox", home_name="New York Yankees",
                 away_code="BOS", home_code="NYY")
    starters = pd.DataFrame([
        {"game_id": "m1", "team": "New York Yankees", "side": "home",
         "starter_id": "p1", "starter_name": "Ace One",
         "outs": 18.0, "k": 8, "bb": 1, "hbp": 0, "hr": 1, "batters_faced": 24},
        {"game_id": "m1", "team": "Boston Red Sox", "side": "away",
         "starter_id": "p2", "starter_name": "Ace Two",
         "outs": 15.0, "k": 3, "bb": 4, "hbp": 1, "hr": 2, "batters_faced": 23},
    ])
    dives = build_deep_dives(
        [card], {"g1": _projection()}, _mlb_games(), None,
        team_names={"BOS": "Boston Red Sox", "NYY": "New York Yankees"},
        starters=starters, league="mlb",
    )
    labels = [r.label for r in dives[0].rows]
    assert labels[0] == "RUNS / GM"
    assert "SP K / 9" in labels and "SP INNINGS / START" in labels
