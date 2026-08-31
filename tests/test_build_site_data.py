"""Site data prep — latest-stamp selection, joins, and typed empty frames."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "build_site_data.py"


def _slate_frames(folder: Path) -> None:
    old, new = "20260101T120000Z", "20260102T120000Z"
    for stamp, price in ((old, -200), (new, -110)):
        pd.DataFrame([{
            "game_id": "g1", "market": "total", "side": "under", "point": 8.5,
            "book": "dk", "price": price, "p_model": 0.568, "p_fair": 0.512,
            "edge": 0.056, "stake": 1.2,
        }]).to_parquet(folder / f"slate_mlb_{stamp}.parquet", index=False)
    pd.DataFrame([{
        "game_id": "g1", "home_team": "Brewers", "away_team": "Cubs",
        "kickoff": pd.Timestamp("2026-01-02 19:10"),
    }]).to_parquet(folder / f"games_mlb_{new}.parquet", index=False)
    pd.DataFrame([{
        "game_id": "g1", "away": "Cubs", "home": "Brewers", "n_sims": 100,
        "mu_away": 3.7, "mu_home": 4.2, "p_home_win": 0.589,
        "fair_spread": -0.5, "fair_total": 7.9,
    }]).to_parquet(folder / f"projections_mlb_{new}.parquet", index=False)
    pd.DataFrame([
        {"section": "games", "play": "CHC@MIL U8.5", "market": "total",
         "side": "under", "point": 8.5, "price": -110.0, "stake": 1.0,
         "result": "win", "profit": 0.91, "slate_date": pd.Timestamp("2026-01-01")},
        {"section": "games", "play": "X@Y", "market": "spread", "side": "home",
         "point": -3.0, "price": -110.0, "stake": 1.0, "result": "loss",
         "profit": -1.0, "slate_date": pd.Timestamp("2026-01-02")},
    ]).to_parquet(folder / f"cumulative_record_mlb_{new}.parquet", index=False)
    # The publish gate's audit — one posted play, one held back with its reason.
    pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "under", "player": None,
         "price": -110.0, "stake": 1.2, "edge": 0.056, "tier": "A",
         "drift": None, "conviction": 0.81, "context": 0.22,
         "published": True, "reason": ""},
        {"game_id": "g1", "market": "moneyline", "side": "home", "player": None,
         "price": -145.0, "stake": 0.8, "edge": 0.041, "tier": "A",
         "drift": None, "conviction": 0.66, "context": -0.05,
         "published": False,
         "reason": "context -0.05 does not corroborate the edge"},
    ]).to_parquet(folder / f"publish_mlb_{new}.parquet", index=False)


def test_build_site_data_end_to_end(tmp_path: Path) -> None:
    slate_dir = tmp_path / "slate"
    slate_dir.mkdir()
    _slate_frames(slate_dir)
    out = tmp_path / "data"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--slate-dir", str(slate_dir),
         "--out", str(out), "--cards-out", str(tmp_path / "cards")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr

    board = pd.read_parquet(out / "board.parquet")
    assert len(board) == 1
    row = board.iloc[0]
    # The newest stamp won, and the joins landed.
    assert row["price"] == -110
    assert row["home_team"] == "Brewers"
    assert row["fair_total"] == 7.9
    assert row["league"] == "mlb"

    units = pd.read_parquet(out / "units.parquet")
    assert units["units"].tolist() == pytest.approx([0.91, -0.09])

    # The plays page's table: the gate's verdicts joined with matchup names,
    # published and held-back rows alike (the reason rides along).
    publish = pd.read_parquet(out / "publish.parquet")
    assert len(publish) == 2
    posted = publish[publish["published"]].iloc[0]
    assert posted["home_team"] == "Brewers" and posted["market"] == "total"
    held = publish[~publish["published"]].iloc[0]
    assert "does not corroborate" in held["reason"]

    # Absent families still produce typed frames every page can query — with
    # exactly one sentinel row, because Evidence's source runner writes no
    # parquet at all for a zero-row query and the build then fails reading
    # the missing extraction.
    dfs = pd.read_parquet(out / "dfs_lineup.parquet")
    assert len(dfs) == 1
    assert dfs.iloc[0]["league"] == "__none__"
    assert "salary" in dfs.columns
    # Every DFS format the site renders needs its own typed sentinel: the
    # page's SQL runs whether or not DK posted that board today.
    for name, column in (("dfs_showdown", "salary"), ("dfs_tiered", "unit")):
        frame = pd.read_parquet(out / f"{name}.parquet")
        assert len(frame) == 1, name
        assert frame.iloc[0]["league"] == "__none__", name
        assert column in frame.columns, name
    record = pd.read_parquet(out / "record.parquet")
    assert record.iloc[0]["league"] == "__none__"
    # Dates stay real datetimes (all-null date columns would get downcast
    # to Float64 by Evidence, changing the extracted column type).
    assert str(record["slate_date"].dtype).startswith("datetime64")


def test_collect_cards_copies_newest_stamp_and_captions(tmp_path: Path) -> None:
    slate_dir = tmp_path / "slate"
    slate_dir.mkdir()
    old, new = "20260101T120000Z", "20260102T120000Z"
    # An older stamp that must lose, and the newest batch with captions.
    (slate_dir / f"social_nfl_{old}_NYJ_at_NE.png").write_bytes(b"old")
    (slate_dir / f"social_nfl_{new}_BUF_at_KC.png").write_bytes(b"png1")
    (slate_dir / f"social_nfl_{new}_DAL_at_PHI.png").write_bytes(b"png2")
    (slate_dir / f"social_nfl_{new}_captions.md").write_text(
        "BUF @ KC — model: BUF 69%.\n\n---\n\nDAL @ PHI — model: PHI 78%.\n")
    (slate_dir / f"recordcard_mlb_{new}.png").write_bytes(b"rec")
    static_out = tmp_path / "static" / "cards"
    (static_out / "stale").mkdir(parents=True)
    (static_out / "stale.png").write_bytes(b"stale")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--slate-dir", str(slate_dir),
         "--out", str(tmp_path / "data"), "--cards-out", str(static_out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr

    copied = sorted(p.name for p in static_out.glob("*.png"))
    assert copied == [f"recordcard_mlb_{new}.png",
                      f"social_nfl_{new}_BUF_at_KC.png",
                      f"social_nfl_{new}_DAL_at_PHI.png"]
    cards = pd.read_parquet(tmp_path / "data" / "cards.parquet")
    assert len(cards) == 3
    buf = cards[cards["file"] == f"social_nfl_{new}_BUF_at_KC.png"].iloc[0]
    assert (buf["away"], buf["home"]) == ("BUF", "KC")
    assert buf["caption"].startswith("BUF @ KC — model")
    rec = cards[cards["kind"] == "recordcard"].iloc[0]
    assert rec["away"] == "" and rec["league"] == "mlb"


def test_build_units_coerces_object_profit(tmp_path: Path) -> None:
    # Real graded frames arrive with profit as object dtype (pending rows mix
    # None upstream) — the crash that failed the first live site build.
    slate_dir = tmp_path / "slate"
    slate_dir.mkdir()
    frame = pd.DataFrame([
        {"section": "games", "play": "A@B", "market": "total", "side": "under",
         "point": 8.5, "price": -110.0, "stake": 1.0, "result": "win",
         "profit": 0.91, "slate_date": pd.Timestamp("2026-01-01")},
        {"section": "games", "play": "C@D", "market": "spread", "side": "home",
         "point": -3.0, "price": -110.0, "stake": 1.0, "result": "pending",
         "profit": None, "slate_date": pd.Timestamp("2026-01-01")},
    ])
    frame["profit"] = frame["profit"].astype(object)
    frame.to_parquet(slate_dir / "cumulative_record_mlb_20260101T120000Z.parquet",
                     index=False)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--slate-dir", str(slate_dir),
         "--out", str(tmp_path / "data"), "--cards-out", str(tmp_path / "cards")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    units = pd.read_parquet(tmp_path / "data" / "units.parquet")
    assert units["units"].tolist() == pytest.approx([0.91])
