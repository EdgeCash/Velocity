"""Elite-hub surfaces: ratings export, CLV closing join, dossier data prep."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent
SITE_SCRIPT = REPO / "scripts" / "build_site_data.py"

sys.path.insert(0, str(REPO / "scripts"))


class _FakeScoresRatings:
    def __init__(self) -> None:
        self.teams = ("A", "B")
        self.offense = {"A": 3.0, "B": -1.0}
        self.defense = {"A": -2.0, "B": 1.5}
        self.base_points = 24.0
        self.home_edge = 1.2


class _FakeScoresModel:
    def __init__(self) -> None:
        self.ratings = _FakeScoresRatings()


def test_ratings_frame_scores_path() -> None:
    from run_live_slate import _ratings_frame

    model = _FakeScoresModel()
    frame = _ratings_frame("wnba", model, model)
    assert list(frame["team"]) == ["A", "B"]  # sorted by net desc
    a = frame.iloc[0]
    assert a["net"] == pytest.approx(5.0)  # off 3.0 − def (−2.0)
    assert a["rank"] == 1
    assert frame.iloc[1]["net"] == pytest.approx(-2.5)


def test_ratings_frame_epa_conversion() -> None:
    from run_live_slate import _epa_ratings_rows

    class _Fit:
        teams = ("KC",)
        offense = {"KC": 0.10}
        defense = {"KC": -0.05}

    rows = _epa_ratings_rows(_Fit(), plays_per_game=63.0)
    assert rows[0]["net"] == pytest.approx(63.0 * 0.15)


def test_closing_for_slate_consensus(tmp_path: Path) -> None:
    from grade_yesterday import closing_for_slate

    kickoff = pd.Timestamp("2026-01-02 18:00")
    games_map = pd.DataFrame([{
        "game_id": "g1", "home_team": "Brewers", "away_team": "Cubs",
        "kickoff": kickoff, "league": "mlb",
    }])
    slate = pd.DataFrame([{
        "game_id": "g1", "market": "spread", "side": "home", "point": -1.5,
        "price": -110.0,
    }])

    def snap(name: str, ts: str, point: float, price: float, book: str) -> None:
        pd.DataFrame([{
            "line_id": "x", "game_id": "g1", "book": book, "market": "spread",
            "side": "Brewers", "price": price, "point": point,
            "timestamp": pd.Timestamp(ts), "is_closing": False,
            "league": "mlb", "collected_at": pd.Timestamp(ts),
        }]).to_parquet(tmp_path / name, index=False)

    # Early snapshot must lose to the pre-kick one; post-kick must be ignored;
    # the close is the cross-book median of the last pre-kick observations.
    snap("odds_lines_1.parquet", "2026-01-02 10:00", -1.5, -105, "dk")
    snap("odds_lines_2.parquet", "2026-01-02 17:30", -2.0, -120, "dk")
    snap("odds_lines_3.parquet", "2026-01-02 17:45", -2.5, -130, "fd")
    snap("odds_lines_4.parquet", "2026-01-02 19:00", -3.5, -150, "dk")

    closing = closing_for_slate(tmp_path, slate, games_map, "mlb")
    assert closing is not None and len(closing) == 1
    row = closing.iloc[0]
    assert row["side"] == "home"
    assert row["point"] == pytest.approx(-2.25)  # median of −2.0, −2.5
    # Price consensus is taken in DECIMAL space (American odds are
    # discontinuous across ±100), so −120/−130 lands at −124.8, not −125.
    assert row["price"] == pytest.approx(-124.8, abs=0.05)


def test_record_carries_clv_columns() -> None:
    from velocity.report.daily_record import RECORD_COLUMNS, build_daily_record

    graded = pd.DataFrame([{
        "game_id": "g1", "market": "spread", "side": "home", "point": -1.5,
        "price": -110.0, "stake": 1.0, "result": "win", "profit": 0.91,
        "price_clv": 0.04, "line_clv": 0.5,
    }])
    record = build_daily_record(graded, None, None,
                                matchups={"g1": "A @ B"},
                                slate_date=pd.Timestamp("2026-01-02"))
    assert "price_clv" in RECORD_COLUMNS
    assert record.iloc[0]["line_clv"] == pytest.approx(0.5)


def test_site_data_new_tables(tmp_path: Path) -> None:
    slate_dir = tmp_path / "slate"
    slate_dir.mkdir()
    stamp = "20260102T120000Z"
    pd.DataFrame([
        {"team": "A", "off": 3.0, "def": -2.0, "net": 5.0, "pace": float("nan"),
         "scale": "pts/gm", "rank": 1},
        {"team": "B", "off": -1.0, "def": 1.5, "net": -2.5, "pace": float("nan"),
         "scale": "pts/gm", "rank": 2},
    ]).to_parquet(slate_dir / f"ratings_wnba_{stamp}.parquet", index=False)
    # A previous run's export, for movement: B was #1.
    prev_dir = tmp_path / "prev"
    prev_dir.mkdir()
    pd.DataFrame([
        {"team": "A", "net": -1.0, "rank": 2},
        {"team": "B", "net": 2.0, "rank": 1},
    ]).to_parquet(prev_dir / "ratings_wnba_20260101T120000Z.parquet", index=False)
    # A card + its manifest → game_id lands on the cards table.
    (slate_dir / f"social_wnba_{stamp}_LV_at_NY.png").write_bytes(b"png")
    pd.DataFrame([{"game_id": "g9", "kind": "social",
                   "file": f"social_wnba_{stamp}_LV_at_NY.png",
                   "league": "wnba"}]).to_parquet(
        slate_dir / f"cardindex_wnba_{stamp}.parquet", index=False)

    result = subprocess.run(
        [sys.executable, str(SITE_SCRIPT), "--slate-dir", str(slate_dir),
         "--out", str(tmp_path / "data"), "--cards-out", str(tmp_path / "cards"),
         "--prev-dir", str(prev_dir), "--odds-dir", str(tmp_path / "no-odds"),
         "--fp-dir", str(tmp_path / "no-fp"), "--no-weather"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr

    ratings = pd.read_parquet(tmp_path / "data" / "ratings.parquet")
    a = ratings[ratings["team"] == "A"].iloc[0]
    assert a["rank_prev"] == pytest.approx(2.0)
    assert a["net_prev"] == pytest.approx(-1.0)
    cards = pd.read_parquet(tmp_path / "data" / "cards.parquet")
    assert cards.iloc[0]["game_id"] == "g9"
    # Absent dossier families still write typed sentinels.
    for table in ("line_moves", "injuries", "weather"):
        frame = pd.read_parquet(tmp_path / "data" / f"{table}.parquet")
        assert len(frame) == 1 and frame.iloc[0]["league"] == "__none__"


def test_consensus_american_straddles_the_gap() -> None:
    from velocity.wagering.odds import consensus_american

    # The first live CLV run crashed on median(-105, +102) = -1.5. The
    # decimal-space consensus stays a valid price.
    price = consensus_american([-105.0, 102.0])
    assert price is not None
    assert not -100.0 < price < 100.0
    assert consensus_american([float("nan"), None]) is None
    assert consensus_american([-110.0, -110.0]) == pytest.approx(-110.0)


def test_closing_consensus_survives_gap_straddling_books(tmp_path: Path) -> None:
    from grade_yesterday import closing_for_slate
    from velocity.report.scorecard import grade_slate

    kickoff = pd.Timestamp("2026-01-02 18:00")
    games_map = pd.DataFrame([{
        "game_id": "g1", "home_team": "Brewers", "away_team": "Cubs",
        "kickoff": kickoff, "league": "mlb",
    }])
    slate = pd.DataFrame([{
        "game_id": "g1", "market": "moneyline", "side": "home", "point": None,
        "price": -104.0, "stake": 1.0, "p_model": 0.55,
    }])
    for i, (price, book) in enumerate([(-105.0, "dk"), (102.0, "fd")]):
        pd.DataFrame([{
            "line_id": "x", "game_id": "g1", "book": book,
            "market": "moneyline", "side": "Brewers", "price": price,
            "point": None, "timestamp": pd.Timestamp("2026-01-02 17:30"),
            "is_closing": False, "league": "mlb",
            "collected_at": pd.Timestamp("2026-01-02 17:30"),
        }]).to_parquet(tmp_path / f"odds_lines_{i}.parquet", index=False)

    closing = closing_for_slate(tmp_path, slate, games_map, "mlb")
    assert closing is not None
    assert not -100.0 < closing.iloc[0]["price"] < 100.0
    finals = pd.DataFrame([{"game_id": "g1", "home_score": 4.0, "away_score": 2.0}])
    graded = grade_slate(slate, finals, closing)  # must not raise
    assert graded.iloc[0]["result"] == "win"
