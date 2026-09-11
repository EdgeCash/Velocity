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
         "result": "win", "profit": 0.91, "line_clv": 0.5,
         "stake_sized": 0.5, "profit_sized": 0.455,
         "slate_date": pd.Timestamp("2026-01-01")},
        {"section": "games", "play": "X@Y", "market": "spread", "side": "home",
         "point": -3.0, "price": -110.0, "stake": 1.0, "result": "loss",
         "profit": -1.0, "line_clv": -1.0, "stake_sized": None, "profit_sized": None,
         "slate_date": pd.Timestamp("2026-01-02")},
        # A team total: graded, but its close is not a yardstick.
        {"section": "games", "play": "X@Y TT", "market": "team_total_home",
         "side": "over", "point": 4.5, "price": -105.0, "stake": 0.0,
         "result": "win", "profit": 0.0, "line_clv": 1.0, "stake_sized": 0.0,
         "profit_sized": 0.0, "slate_date": pd.Timestamp("2026-01-02")},
    ]).to_parquet(folder / f"cumulative_record_mlb_{new}.parquet", index=False)
    # The sized card: what the run recommended after the caps, with the
    # bankroll and slate cap it was sized against.
    pd.DataFrame([{
        "game_id": "g1", "market": "total", "side": "under", "kind": "game",
        "price": -110.0, "edge": 0.056, "stake": 0.9, "stake_solo": 1.2,
        "bankroll": 100.0, "slate_cap": 0.25,
    }]).to_parquet(folder / f"portfolio_mlb_{new}.parquet", index=False)
    pd.DataFrame([{
        "legs": "CHC@MIL U8.5 + X@Y home -3", "n_legs": 2, "price": 264.0,
        "decimal": 3.64, "p_win": 0.31, "ev": 0.128, "same_game": False,
        "stake": 0.3, "legs_json": "[]",
    }]).to_parquet(folder / f"slate_mlb_parlays_{new}.parquet", index=False)
    pd.DataFrame([
        {"label": "Ratings", "detail": "pitcher decomposition"},
        {"label": "Paper", "detail": "team totals"},
    ]).to_parquet(folder / f"config_mlb_{new}.parquet", index=False)
    pd.DataFrame([{
        "game_id": "g1", "market": "total", "side": "under", "player": None,
        "tier": "A", "conviction": 0.81,
        "rationale": "Both bullpens rested; wind in at 12 mph.",
    }]).to_parquet(folder / f"intel_mlb_{new}.parquet", index=False)
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

    # The money columns: the sized stake, the venue, the intel argument.
    assert row["stake_sized"] == pytest.approx(0.9)
    assert row["venue"] == "sportsbook"
    assert row["rationale"].startswith("Both bullpens")

    units = pd.read_parquet(out / "units.parquet")
    assert units["units"].tolist() == pytest.approx([0.91, -0.09])
    # Sized units: the sized profit where the chain carries it, the solo
    # profit where it predates sizing (the spread row).
    assert units["units_sized"].tolist() == pytest.approx([0.455, -0.545])

    # Per-market CLV carries the trust flag: the team total's CLV is not a
    # yardstick and the page says "judge on P/L" for it.
    clv = pd.read_parquet(out / "clv_by_market.parquet").set_index("market")
    assert bool(clv.loc["total", "clv_trusted"]) is True
    assert bool(clv.loc["team_total_home", "clv_trusted"]) is False
    assert clv.loc["total", "mean_line_clv"] == pytest.approx(0.5)
    assert clv.loc["spread", "units"] == pytest.approx(-1.0)

    # Exposure: the sized card against the cap it was sized under.
    exposure = pd.read_parquet(out / "exposure.parquet")
    assert len(exposure) == 1
    assert exposure.iloc[0]["stake_sized"] == pytest.approx(0.9)
    assert exposure.iloc[0]["cap_units"] == pytest.approx(25.0)
    assert exposure.iloc[0]["bets"] == 1

    parlays = pd.read_parquet(out / "parlays.parquet")
    assert len(parlays) == 1 and parlays.iloc[0]["league"] == "mlb"
    assert parlays.iloc[0]["n_legs"] == 2

    # The Methods block comes from the run's own config export.
    config = pd.read_parquet(out / "model_config.parquet")
    assert set(config["label"]) == {"Ratings", "Paper"}
    assert config["league"].unique().tolist() == ["mlb"]

    # The plays page's table: the gate's verdicts joined with matchup names,
    # published and held-back rows alike (the reason rides along).
    publish = pd.read_parquet(out / "publish.parquet")
    assert len(publish) == 2
    posted = publish[publish["published"]].iloc[0]
    assert posted["home_team"] == "Brewers" and posted["market"] == "total"
    assert posted["stake_sized"] == pytest.approx(0.9)
    held = publish[~publish["published"]].iloc[0]
    assert held["stake_sized"] == 0.0  # the card never sized it
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
    assert "stake_sized" in record.columns and "profit_sized" in record.columns
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
    # A chain without sized columns still renders: the sized line falls
    # back to the solo profit and the page SQL finds its columns.
    assert units["units_sized"].tolist() == pytest.approx([0.91])
    chain = pd.read_parquet(tmp_path / "data" / "cumulative_record.parquet")
    assert chain["profit_sized"].isna().all()
    # No sized card, no parlays: the money tables still carry their typed
    # sentinel row so every page query parses.
    for name in ("exposure", "parlays"):
        frame = pd.read_parquet(tmp_path / "data" / f"{name}.parquet")
        assert len(frame) == 1 and frame.iloc[0]["league"] == "__none__", name
    clv = pd.read_parquet(tmp_path / "data" / "clv_by_market.parquet")
    assert clv.iloc[0]["market"] == "total" and bool(clv.iloc[0]["clv_trusted"])


def test_ledger_tables_ride_into_the_site(tmp_path: Path) -> None:
    from velocity.wagering.ledger import Ledger

    slate_dir = tmp_path / "slate"
    slate_dir.mkdir()
    _slate_frames(slate_dir)
    book = Ledger(path=tmp_path / "ledger.parquet")
    at = pd.Timestamp("2026-01-01 12:00")
    book.seed(100.0, at=at)
    book.recommend(pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "under", "point": 8.5, "book": "dk",
         "price": -110.0, "stake": 2.0, "p_model": 0.568, "kind": "game"},
        {"game_id": "g2", "market": "spread", "side": "home", "point": -1.5, "book": "fd",
         "price": 100.0, "stake": 1.0, "p_model": 0.55, "kind": "game"},
    ]), league="mlb", stamp="20260101T120000Z", at=at)
    book.place("mlb|g1|total|under|", 2.0, at=at, note="auto: booked at the recommended terms")
    book.place("mlb|g2|spread|home|", 1.0, at=at, note="auto: booked at the recommended terms")
    book.settle(pd.DataFrame([{"bet_id": "mlb|g1|total|under|", "result": "win"}]),
                at=at + pd.Timedelta(days=1))
    book.save()

    out = tmp_path / "data"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--slate-dir", str(slate_dir), "--out", str(out),
         "--cards-out", str(tmp_path / "cards"), "--ledger", str(book.path)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    bankroll = pd.read_parquet(out / "bankroll.parquet")
    assert len(bankroll) == 1
    row = bankroll.iloc[0]
    assert row["seed"] == 100.0
    assert row["current"] == pytest.approx(100.0 + 2.0 * 100 / 110)
    assert row["open_exposure"] == 1.0 and row["open_bets"] == 1
    assert row["mode"] == "auto" and not bool(row["halted"])
    assert row["league"] == "all"
    curve = pd.read_parquet(out / "bankroll_curve.parquet")
    assert curve["record_type"].tolist() == ["seed", "settled"]
    assert curve["league"].tolist() == ["all", "mlb"]  # the seed rides through the filter
    open_ = pd.read_parquet(out / "ledger_open.parquet")
    assert open_["bet_id"].tolist() == ["mlb|g2|spread|home||-1.5"]

    # Without a ledger the tables are typed sentinels, so the pages parse.
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--slate-dir", str(slate_dir), "--out", str(out),
         "--cards-out", str(tmp_path / "cards")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    for name in ("bankroll", "bankroll_curve", "ledger_open"):
        frame = pd.read_parquet(out / f"{name}.parquet")
        assert len(frame) == 1 and frame.iloc[0]["league"] == "__none__"


def test_market_health_rides_into_the_site(tmp_path: Path) -> None:
    from velocity.report.monitor import market_health

    slate_dir = tmp_path / "slate"
    slate_dir.mkdir()
    _slate_frames(slate_dir)
    as_of = pd.Timestamp("2026-01-02")
    chain = pd.DataFrame([
        {"market": "total", "side": "under", "stake": 1.0, "result": "win" if i % 2 else "loss",
         "profit": 0.91 if i % 2 else -1.0, "line_clv": -0.6, "price_clv": None,
         "p_model": 0.56, "slate_date": as_of - pd.Timedelta(days=i % 5)}
        for i in range(24)
    ])
    health = market_health(chain, as_of=as_of)
    health.assign(league="mlb", as_of=as_of).to_parquet(
        slate_dir / "monitor_mlb_20260102T120000Z.parquet", index=False)
    out = tmp_path / "data"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--slate-dir", str(slate_dir), "--out", str(out),
         "--cards-out", str(tmp_path / "cards")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    table = pd.read_parquet(out / "market_health.parquet")
    assert set(table["window_days"]) == {7, 30}
    row = table[table["window_days"] == 30].iloc[0]
    assert row["market"] == "total" and row["league"] == "mlb" and row["n_bets"] == 24
    assert bool(row["flag_negative_clv"]) is True and "negative CLV" in row["flags"]
    # The record now carries what the model claimed, for the drift read.
    record = pd.read_parquet(out / "cumulative_record.parquet")
    assert "p_model" in record.columns and "p_fair" in record.columns
