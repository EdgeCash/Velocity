"""The operator's ledger CLI — seed, place, skip, settle, adjust, merge."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from velocity.wagering.ledger import Ledger

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "ledger.py"


def _cli(ledger: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--ledger", str(ledger), *args],
        capture_output=True, text=True, cwd=REPO,
    )


def test_operator_loop_seed_place_settle_adjust(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.parquet"
    assert _cli(ledger, "seed", "--amount", "200").returncode == 0
    # A second seed is refused, not stacked.
    assert _cli(ledger, "seed", "--amount", "999").returncode != 0

    # A bet the card never listed needs its fields; the id it prints is the
    # handle for everything after.
    placed = _cli(ledger, "place", "--stake", "3", "--price", "-105", "--book", "fanduel",
                  "--league", "nfl", "--game-id", "abc", "--market", "total", "--side", "under",
                  "--point", "44.5")
    assert placed.returncode == 0, placed.stderr
    assert "placed nfl|abc|total|under|: 3.00 at -105.0 (fanduel)" in placed.stdout
    # A second placement of the same bet finds its terms from the first.
    again = _cli(ledger, "place", "--bet-id", "nfl|abc|total|under|", "--stake", "1")
    assert again.returncode == 0, again.stderr
    assert "1.00 at -105.0" in again.stdout
    assert "open exposure 4.00 across 1 bet(s)" in again.stdout

    settled = _cli(ledger, "settle", "--bet-id", "nfl|abc|total|under|", "--result", "win")
    assert settled.returncode == 0, settled.stderr
    assert f"{4 * 100 / 105:+.2f}" in settled.stdout
    # Settling it again is refused: it is no longer open.
    assert _cli(ledger, "settle", "--bet-id", "nfl|abc|total|under|",
                "--result", "loss").returncode != 0

    assert _cli(ledger, "adjust", "--amount", "-20", "--note", "withdrawal").returncode == 0
    shown = _cli(ledger, "show")
    assert shown.returncode == 0
    expected = 200.0 + 4 * 100 / 105 - 20.0
    assert f"bankroll {expected:.2f}" in shown.stdout
    assert f"peak {200.0 + 4 * 100 / 105:.2f}" in shown.stdout
    assert "settled P&L by league" in shown.stdout

    loaded = Ledger.load(ledger)
    assert loaded.current_bankroll() == pytest.approx(expected)
    assert loaded.frame["record_type"].tolist() == [
        "seed", "placed", "placed", "settled", "adjust"]


def test_skip_and_todo_read_the_card(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.parquet"
    book = Ledger(path=ledger)
    book.seed(100.0, at=pd.Timestamp("2026-09-10"))
    book.recommend(pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "under", "point": 44.5,
         "book": "dk", "price": -110.0, "stake": 2.0, "p_model": 0.56, "kind": "game"},
        {"game_id": "g2", "market": "spread", "side": "home", "point": -3.0,
         "book": "fd", "price": -108.0, "stake": 1.0, "p_model": 0.55, "kind": "game"},
    ]), league="nfl", stamp="20260910T140000Z", at=pd.Timestamp("2026-09-10"))
    book.save()

    assert _cli(ledger, "skip", "--bet-id", "nfl|g2|spread|home|").returncode == 0
    todo = _cli(ledger, "todo", "--league", "nfl")
    assert todo.returncode == 0, todo.stderr
    assert "1 open, 0 placed, 1 skipped" in todo.stdout
    assert "open nfl|g1|total|under|" in " ".join(todo.stdout.split())
    # Placing from the card takes the recommended terms.
    placed = _cli(ledger, "place", "--bet-id", "nfl|g1|total|under|", "--stake", "2")
    assert "2.00 at -110.0 (dk)" in placed.stdout
    assert Ledger.load(ledger).open_exposure() == 2.0


def test_merge_unions_every_copy_once(tmp_path: Path) -> None:
    base = Ledger(path=tmp_path / "a.parquet")
    base.seed(100.0, at=pd.Timestamp("2026-09-10"))
    base.save()
    other = Ledger.load(tmp_path / "a.parquet")
    other.adjust(10.0, at=pd.Timestamp("2026-09-11"))
    other.save(tmp_path / "b.parquet")
    merged = tmp_path / "merged.parquet"
    result = _cli(tmp_path / "unused.parquet", "merge", "--into", str(merged),
                  str(tmp_path / "a.parquet"), str(tmp_path / "b.parquet"),
                  str(tmp_path / "missing.parquet"), str(tmp_path / "a.parquet"))
    assert result.returncode == 0, result.stderr
    assert "merged 3 cop(y/ies)" in result.stdout
    loaded = Ledger.load(merged)
    assert len(loaded) == 2 and loaded.current_bankroll() == 110.0
    # Merging again changes nothing: the union is idempotent.
    _cli(tmp_path / "unused.parquet", "merge", "--into", str(merged), str(tmp_path / "b.parquet"))
    assert len(Ledger.load(merged)) == 2
