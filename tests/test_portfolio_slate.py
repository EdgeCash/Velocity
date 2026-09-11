"""Portfolio sizing in the live runner — the combined card, end-to-end offline."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "run_live_slate.py"
SNAPSHOT = REPO / "tests" / "fixtures" / "theoddsapi_nfl.json"


def _run(tmp_path: Path, *extra: str) -> tuple[subprocess.CompletedProcess, Path]:
    out = tmp_path / "slate"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--league", "nfl", "--data", "datasets/nfl",
         "--offline", "--snapshot-file", str(SNAPSHOT), "--n-sims", "1000", "--max-days", "0",
         "--min-edge", "0.0", "--no-intel", "--out", str(out), *extra],
        capture_output=True, text=True, cwd=REPO,
    )
    return result, out


def test_portfolio_card_sizes_the_combined_slate(tmp_path: Path) -> None:
    result, out = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Portfolio-sized card" in result.stdout

    files = list(out.glob("portfolio_nfl_*.parquet"))
    assert files, result.stdout
    card = pd.read_parquet(files[0])
    assert {"game_id", "market", "side", "stake", "stake_solo", "kind"} <= set(card.columns)
    # The aggregate cap binds: sized total ≤ 25% of the default 100 bankroll.
    assert card["stake"].sum() <= 25.0 + 1e-6
    # Sizing only ever shrinks: correlation de-scaling and caps never add.
    assert (card["stake"] <= card["stake_solo"] + 1e-9).all()
    # The per-slate parquet keeps its solo-Kelly stakes for comparability.
    slate = pd.read_parquet(next(iter(out.glob("slate_nfl_2*.parquet"))))
    merged = card[card["kind"] == "game"].merge(
        slate, on=["game_id", "market", "side"], suffixes=("", "_slate")
    )
    assert (merged["stake_solo"] == merged["stake_slate"]).all()


def test_portfolio_stage_can_be_disabled(tmp_path: Path) -> None:
    result, out = _run(tmp_path, "--no-portfolio")
    assert result.returncode == 0, result.stderr
    assert "Portfolio-sized card" not in result.stdout
    assert not list(out.glob("portfolio_nfl_*.parquet"))


def _fake_args(tmp_path: Path, **overrides: object) -> object:
    import argparse

    base = {"bankroll": 100.0, "max_slate_fraction": 0.25, "league": "nfl",
            "out": str(tmp_path), "ledger_mode": "auto"}
    base.update(overrides)
    return argparse.Namespace(**base)


def _runner():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_live_slate_ledger", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _card_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "under", "point": 44.5, "book": "dk",
         "price": -110.0, "p_model": 0.56, "p_fair": 0.51, "edge": 0.05, "stake": 3.0,
         "note": None},
        {"game_id": "g2", "market": "spread", "side": "home", "point": -3.0, "book": "fd",
         "price": -108.0, "p_model": 0.55, "p_fair": 0.50, "edge": 0.05, "stake": 2.0,
         "note": None},
    ])


def test_ledger_seeds_stakes_and_books_the_card_end_to_end(tmp_path: Path) -> None:
    from velocity.wagering.ledger import Ledger

    ledger = tmp_path / "ledger.parquet"
    result, out = _run(tmp_path, "--ledger", str(ledger), "--ledger-mode", "auto",
                       "--bankroll", "250")
    assert result.returncode == 0, result.stderr
    assert "ledger: seeded" in result.stdout and "at 250.00" in result.stdout
    book = Ledger.load(ledger)
    assert book.seed_amount() == 250.0
    card = pd.read_parquet(next(iter(out.glob("portfolio_nfl_*.parquet"))))
    # Every staked row was recommended and, in auto mode, booked at its terms.
    rec = book.frame[book.frame["record_type"] == "recommended"]
    assert len(rec) == len(card)
    assert book.open_exposure() == pytest.approx(float(card["stake"].sum()))
    assert (card["bankroll"] == 250.0).all() and not card["halted"].any()
    assert "placed (auto)" in result.stdout


def test_portfolio_card_holds_open_bets_and_counts_exposure_elsewhere(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from velocity.wagering.ledger import Ledger

    runner = _runner()
    book = Ledger(path=tmp_path / "ledger.parquet")
    at = pd.Timestamp("2026-09-10 12:00")
    book.seed(100.0, at=at)
    # g1's under is already on the books from Wednesday's card; a third bet
    # on a game off today's card holds 20 units of the 25-unit slate cap.
    book.recommend(_card_frame().head(1), league="nfl", stamp="20260909T120000Z", at=at)
    book.place("nfl|g1|total|under|", 2.0, at=at)
    book.place(None, 20.0, price=-110.0, at=at,
               fields={"league": "nfl", "game_id": "g0", "market": "total", "side": "over"})
    args = _fake_args(tmp_path)
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    runner._portfolio_card(args, _card_frame(), None, now,
                           pd.Timestamp(now).tz_localize(None), ledger=book)
    card = pd.read_parquet(next(iter(tmp_path.glob("portfolio_nfl_*.parquet"))))
    assert card.set_index("game_id")["held"].to_dict() == {"g1": True, "g2": False}
    # Room under the cap is 25 − 20 = 5 units: the sized total fits inside it.
    assert card["stake"].sum() <= 5.0 + 1e-6
    assert not card["halted"].any()
    # Auto mode placed only the bet that was not already open.
    placed = book.frame[book.frame["record_type"] == "placed"]
    assert placed["bet_id"].tolist() == ["nfl|g1|total|under|", "nfl|g0|total|over|",
                                         "nfl|g2|spread|home|"]
    assert len(book.open_bets()) == 3


def test_portfolio_card_halts_past_the_drawdown_threshold(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from velocity.wagering.ledger import Ledger

    runner = _runner()
    book = Ledger(path=tmp_path / "ledger.parquet")
    at = pd.Timestamp("2026-09-01")
    book.seed(100.0, at=at)
    row = book.place(None, 35.0, price=-110.0, at=at,
                     fields={"league": "nfl", "game_id": "old", "market": "total",
                             "side": "over"})
    book.settle(pd.DataFrame([{"bet_id": row["bet_id"], "result": "loss"}]), at=at)
    assert book.drawdown() == pytest.approx(0.35)
    args = _fake_args(tmp_path, bankroll=65.0)
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        runner._portfolio_card(args, _card_frame(), None, now,
                               pd.Timestamp(now).tz_localize(None), ledger=book)
    assert "KILL-SWITCH — halted: drawdown 35% ≥ 30%" in buf.getvalue()
    card = pd.read_parquet(next(iter(tmp_path.glob("portfolio_nfl_*.parquet"))))
    assert (card["stake"] == 0.0).all() and card["halted"].all()
    assert card["note"].str.startswith("halted: drawdown").all()
    # Recorded, placed nowhere: the ledger's exposure stays zero.
    assert int((book.frame["record_type"] == "recommended").sum()) == 2
    assert book.open_exposure() == 0.0


def test_a_sizing_failure_cannot_hide_the_kill_switch(tmp_path: Path) -> None:
    """The halt is read before the guard, so nothing below can swallow it.

    Every line of `_portfolio_card` runs under a broad `except` so a sizing
    failure never breaks the slates. That guard used to take the kill-switch
    with it: a bankroll past the drawdown threshold went unannounced because
    something unrelated raised a few lines earlier, and all the operator saw
    was `portfolio sizing skipped: ...` — a line that reads like housekeeping.
    """
    import contextlib
    import io
    from datetime import UTC, datetime

    from velocity.wagering.ledger import Ledger

    runner = _runner()
    book = Ledger(path=tmp_path / "ledger.parquet")
    at = pd.Timestamp("2026-09-01")
    book.seed(100.0, at=at)
    row = book.place(None, 35.0, price=-110.0, at=at,
                     fields={"league": "nfl", "game_id": "old", "market": "total",
                             "side": "over"})
    book.settle(pd.DataFrame([{"bet_id": row["bet_id"], "result": "loss"}]), at=at)
    assert book.drawdown() == pytest.approx(0.35)

    # A card with no `stake` column raises inside the guard, well before the
    # ledger is consulted — the shape of the failure that used to go quiet.
    broken = _card_frame().drop(columns=["stake"])
    args = _fake_args(tmp_path, bankroll=65.0)
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        runner._portfolio_card(args, broken, None, now,
                               pd.Timestamp(now).tz_localize(None), ledger=book)
    out = buf.getvalue()
    assert "PORTFOLIO SIZING FAILED" in out, "a failure must not read like a note"
    assert "KILL-SWITCH — halted: drawdown 35%" in out, (
        "the halt is read before the guard and must survive the failure"
    )
    assert "do not stake from them unsized" in out
    # Nothing was written, which is the safe direction.
    assert not list(tmp_path.glob("portfolio_nfl_*.parquet"))


def test_the_venue_cap_survives_the_open_exposure_rebuild(tmp_path: Path) -> None:
    """Room left under the slate cap rebuilds the config — with its caps intact.

    On a day with money already on the table the slate cap shrinks to the room
    remaining, and the config is rebuilt for it. Constructing a bare one there
    dropped the exchange cap on exactly the days it matters most.
    """
    from velocity.wagering.portfolio import PortfolioConfig

    rebuilt = PortfolioConfig(max_portfolio_fraction=0.05,
                              venue_caps=PortfolioConfig(
                                  venue_caps={"exchange": 0.25}).venue_caps)
    assert rebuilt.venue_caps == {"exchange": 0.25}
    source = (Path(__file__).resolve().parents[1] / "scripts" / "run_live_slate.py").read_text()
    assert "venue_caps=config.venue_caps" in source, (
        "the open-exposure rebuild must carry the venue caps forward"
    )


def test_a_row_crowded_out_by_another_venue_says_so(tmp_path: Path) -> None:
    """The inert exchange go-live, made visible.

    A `bet_id` is a view, so an exchange rung and a sportsbook number on the
    same game/market/side are one bet and the rung is held rather than placed.
    That is the right call — the view is already on the books, and making the
    id name the number instead would re-place on every line tick. What was
    wrong is that it happened in silence: the card showed a full stake, the log
    said "N already on the books", and a whole venue never placed with nothing
    saying why. Nineteen exchange rows across NFL and NCAAF went that way on
    the first live run, and one bet was placed all night.
    """
    import contextlib
    import io
    from datetime import UTC, datetime

    from velocity.wagering.ledger import Ledger

    runner = _runner()
    book = Ledger(path=tmp_path / "ledger.parquet")
    at = pd.Timestamp("2026-09-10")
    book.seed(100.0, at=at)
    # Already on the books: the sportsbook's number on g1's under.
    book.place(None, 1.10, price=-110.0, at=at, book="lowvig", point=44.5,
               fields={"league": "nfl", "game_id": "g1", "market": "total", "side": "under"})

    # Today's card wants the same view as a Kalshi rung at a different number.
    card = _card_frame()
    card.loc[0, ["book", "point", "price"]] = ["kalshi", 25.5, 900.0]
    args = _fake_args(tmp_path)
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        runner._portfolio_card(args, card, None, now,
                               pd.Timestamp(now).tz_localize(None), ledger=book)
    out = buf.getvalue()
    assert "held at a different venue" in out, out
    assert "kalshi total under 25.5 held by lowvig 44.5" in out, out

    sized = pd.read_parquet(next(iter(tmp_path.glob("portfolio_nfl_*.parquet"))))
    row = sized[sized["book"] == "kalshi"].iloc[0]
    assert bool(row["held"]) and row["held_by"] == "lowvig 44.5"
    # The row on the same venue as the open bet is held too, but that is the
    # ordinary case and is not called out as a crowding.
    assert "fd spread home" not in out
