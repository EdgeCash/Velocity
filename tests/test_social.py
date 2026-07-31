"""Social model cards — data assembly, watch selection, captions, and the PNG.

The card is the public face of the model, so its rules are pinned exactly:
facts only (probabilities and averages, never a price), board-lined
disagreements outrank median-anchored fallbacks, hits facts are board-only
(the fallback version is the same sentence for every batter), one entry per
player, and the render is the fixed 1200×675 frame.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
from velocity.models.game_mlb import MLBProjection
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import GameSim
from velocity.models.simulate_baseball import BaseballSimResult
from velocity.report.social import build_social_cards, caption
from velocity.report.social_png import card_filename, render_card


def _sim(home: list[float], away: list[float]) -> GameSim:
    return GameSim(home_score=np.array(home, float), away_score=np.array(away, float))


def _projection() -> MLBProjection:
    full = _sim([5, 4, 2, 1], [3, 2, 4, 3])  # margins +2 +2 −2 −2 → P(home)=0.5
    f5 = _sim([2, 2, 1, 1], [1, 1, 2, 2])
    i1 = _sim([1, 0, 0, 0], [0, 0, 0, 0])
    result = BaseballSimResult(
        full=full, f5=f5, i1=i1,
        pitcher_strikeouts={"p1": np.array([4, 6, 8, 6], float)},
        batter_total_bases={"b1": np.array([0, 2, 1, 3], float)},
        batter_hits={"b1": np.array([0, 1, 1, 2], float)},
    )

    def _gp(sim: GameSim) -> GameProjection:
        return GameProjection(
            "LAD", "SF", float(sim.home_score.mean()), float(sim.away_score.mean()), sim
        )

    return MLBProjection(full=_gp(full), f5=_gp(f5), i1=_gp(i1), result=result)


EVENTS = pd.DataFrame([{
    "game_id": "evt1",
    "away_team": "San Francisco Giants",
    "home_team": "Los Angeles Dodgers",
    "kickoff": pd.Timestamp("2026-07-27 02:10:00"),
}])
NAMES = {"p1": "Logan Webb", "b1": "Shohei Ohtani"}


def _cards(**kw: object) -> list:
    return build_social_cards({"evt1": _projection()}, EVENTS, **kw)  # type: ignore[arg-type]


def test_card_states_model_facts() -> None:
    card = _cards(id_to_name=NAMES)[0]
    assert (card.away_code, card.home_code) == ("SF", "LAD")
    assert card.p_home_win == pytest.approx(0.5)
    assert card.p_yrfi == pytest.approx(0.25)  # one first-inning run in four sims
    assert sum(card.total_runs_pmf.values()) == pytest.approx(1.0)
    # Totals are 8, 6, 6, 4 → the pmf carries exactly those buckets.
    assert card.total_runs_pmf[6] == pytest.approx(0.5)


def test_watch_fallback_prefers_strikeouts_and_skips_hits() -> None:
    card = _cards(id_to_name=NAMES)[0]
    markets = [entry.market for entry in card.watch]
    # No board: the starter's K fact leads, TB follows, hits never appears.
    assert markets == ["pitcher_strikeouts", "total_bases"]
    webb = card.watch[0]
    assert webb.player == "Logan Webb"
    assert webb.line == pytest.approx(6.5)  # K samples 4,6,8,6 → floor(median 6) + 0.5
    assert webb.p_over == pytest.approx(0.25)  # only the 8-K sim clears 6.5
    assert webb.from_board is False


def test_watch_board_line_leads_and_carries_the_market_number() -> None:
    board = pd.DataFrame([{
        "game_id": "evt1", "market": "total_bases", "player": "Shohei Ohtani",
        "side": side, "point": 1.5, "price": -110, "book": "dk",
        "timestamp": pd.Timestamp("2026-07-26 18:00"), "is_closing": False,
        "line_id": f"x-{side}",
    } for side in ("over", "under")])
    card = _cards(id_to_name=NAMES, prop_lines=board)[0]
    first = card.watch[0]
    assert first.player == "Shohei Ohtani"
    assert first.from_board is True
    assert first.line == pytest.approx(1.5)
    assert first.p_over == pytest.approx(0.5)  # TB samples 0,2,1,3 → two above 1.5


def test_watch_needs_a_display_name() -> None:
    card = _cards()[0]  # no id→name map (offline): nothing nameable → empty strip
    assert card.watch == ()


def test_caption_is_factual_and_priceless() -> None:
    text = caption(_cards(id_to_name=NAMES)[0])
    assert "SF @ LAD" in text
    assert "fair total" in text
    assert "first-inning run 25%" in text
    assert "Logan Webb:" in text
    # No odds, no imperatives — facts only.
    for banned in ("+1", "-1", "bet", "tail", "smash", "lock", "play"):
        assert banned not in text.lower()


def test_render_card_writes_the_fixed_frame(tmp_path: Path) -> None:
    card = _cards(id_to_name=NAMES)[0]
    path = render_card(card, tmp_path / "card.png")
    image = plt.imread(path)
    assert image.shape[0] == 900  # 16:9 at 1600×900 — uncropped in the X timeline
    assert image.shape[1] == 1600


def test_distributions_frame_is_a_pmf_per_game_and_kind() -> None:
    from velocity.report.social import distributions_frame

    frame = distributions_frame({"evt1": _projection()})
    for kind in ("total", "margin"):
        part = frame[frame["kind"] == kind]
        assert part["prob"].sum() == pytest.approx(1.0)
    # Margins carry their true signed support — sims 2, 2, −2, −2.
    margins = frame[frame["kind"] == "margin"]
    assert set(margins["value"]) == {-2, 2}
    totals = frame[frame["kind"] == "total"]
    assert set(totals["value"]) == {4, 6, 8}  # totals 8, 6, 6, 4


def test_record_line_rides_every_card() -> None:
    card = _cards(id_to_name=NAMES, record_line="SEASON 3-1 · +2.1U")[0]
    assert card.record_line == "SEASON 3-1 · +2.1U"


def test_market_strip_condenses_the_board() -> None:
    from velocity.report.social import market_strip

    lines = pd.DataFrame([
        {"game_id": "evt1", "market": "moneyline", "side": "away", "price": 142, "point": None},
        {"game_id": "evt1", "market": "moneyline", "side": "away", "price": 138, "point": None},
        {"game_id": "evt1", "market": "moneyline", "side": "home", "price": -156, "point": None},
        {"game_id": "evt1", "market": "total", "side": "over", "price": -110, "point": 8.5},
        {"game_id": "evt1", "market": "total", "side": "under", "price": -110, "point": 8.5},
    ])
    strip = market_strip(lines, "evt1", "SF", "LAD")
    assert strip == "SF +140 · LAD -156 · O/U 8.5"  # median ML, modal total
    assert market_strip(lines, "other", "A", "B") is None
    assert market_strip(None, "evt1", "SF", "LAD") is None


def test_team_context_rides_the_card() -> None:
    card = _cards(
        id_to_name=NAMES,
        team_records={"SF": "54-52", "LAD": "63-44"},
        lines=pd.DataFrame([
            {"game_id": "evt1", "market": "total", "side": "over", "price": -110,
             "point": 8.5},
        ]),
    )[0]
    assert card.away_record == "54-52"
    assert card.home_record == "63-44"
    assert card.market == "O/U 8.5"
    # Watch entries carry the model player id — the card's headshot key.
    assert card.watch[0].player_id in {"p1", "b1"}


def test_card_filename_is_safe() -> None:
    card = _cards()[0]
    assert card_filename(card, "20260727T140000Z") == "social_mlb_20260727T140000Z_SF_at_LAD.png"
