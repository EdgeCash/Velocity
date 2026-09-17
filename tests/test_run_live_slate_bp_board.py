"""BettingPros game board — end-to-end offline smoke.

The collector has banked multi-book BettingPros lines every three hours since
it was built, and until this path existed nothing read them: the live board
came from The Odds API alone, so a BettingPros price was never shopped and
never graded. This runs the real CLI against a banked BP snapshot and asserts
its rows reach the card, re-keyed onto the sportsbook board's games, prefixed
so the feed stays identifiable, and staked at zero by default.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "run_live_slate.py"
SNAPSHOT = REPO / "tests" / "fixtures" / "theoddsapi_nfl.json"

# The frozen snapshot's first game, and a collection stamp close enough to its
# kickoff that the board is inside the age gate on any run.
KICKOFF = pd.Timestamp("2026-09-11 00:20:00")
COLLECTED = KICKOFF - pd.Timedelta(minutes=30)


def _bp_events() -> pd.DataFrame:
    return pd.DataFrame([{
        "game_id": "bp-9001",
        "home_team": "Kansas City Chiefs",
        "away_team": "Buffalo Bills",
        "kickoff": KICKOFF,
        "league": "nfl",
        "collected_at": COLLECTED,
    }])


def _bp_lines() -> pd.DataFrame:
    """A two-sided BP moneyline and total on the same game, by nickname.

    Nicknames on purpose: that is how BettingPros labels selections, and it is
    the shape that silently dropped every spread and moneyline before the
    within-game side resolution landed.
    """
    rows = [
        ("moneyline", "Chiefs", -160, None),
        ("moneyline", "Bills", 140, None),
        ("total", "Over", -108, 47.5),
        ("total", "Under", -112, 47.5),
    ]
    return pd.DataFrame([
        {
            "line_id": f"bp-9001|{market}|{side}|10|{point or ''}",
            "game_id": "bp-9001",
            "book": "10",
            "market": market,
            "side": side,
            "price": price,
            "point": point,
            "timestamp": COLLECTED,
            "is_closing": False,
            "league": "nfl",
            "collected_at": COLLECTED,
        }
        for market, side, price, point in rows
    ])


def _run(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    lines_path = tmp_path / "bp_lines.parquet"
    events_path = tmp_path / "bp_events.parquet"
    books_path = tmp_path / "bp_books.parquet"
    _bp_lines().to_parquet(lines_path, index=False)
    _bp_events().to_parquet(events_path, index=False)
    pd.DataFrame([{"book_id": "10", "book_name": "draftkings"}]).to_parquet(
        books_path, index=False
    )
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--league", "nfl", "--data", "datasets/nfl",
         "--offline", "--snapshot-file", str(SNAPSHOT),
         "--bp-lines-file", str(lines_path), "--bp-events-file", str(events_path),
         "--bp-books-file", str(books_path),
         "--n-sims", "2000", "--max-days", "0", "--min-edge", "0.0",
         # The fixture's lines are spreads and moneylines, which the selection
         # round keeps off the board at a zero weight (docs/OUTPUT_AUDIT.md);
         # this test is about the venue's plumbing, so put them back.
         "--model-weight-market", "spread=0.2", "--model-weight-market", "moneyline=0.2",
         "--out", str(tmp_path / "slate"), *extra],
        capture_output=True, text=True, cwd=REPO,
    )


def _slate(tmp_path: Path) -> pd.DataFrame:
    files = list((tmp_path / "slate").glob("slate_nfl_2*.parquet"))
    assert files, "no game slate was written"
    return pd.read_parquet(sorted(files)[-1])


def test_bp_board_reaches_the_card_and_is_papered_by_default(tmp_path: Path) -> None:
    result = _run(tmp_path, "--bp-max-age-min", "100000")
    assert result.returncode == 0, result.stderr
    assert "bettingpros:" in result.stdout, result.stdout
    assert "staked at zero" in result.stdout, result.stdout

    slate = _slate(tmp_path)
    bp_rows = slate[slate["book"].astype(str).str.startswith("bp:")]
    assert not bp_rows.empty, result.stdout
    # The banked id resolved to a name through the books listing.
    assert set(bp_rows["book"]) == {"bp:draftkings"}
    # Re-keyed onto the sportsbook board's own game id, so the price is
    # shopped against the books rather than sitting on a duplicate game.
    assert set(bp_rows["game_id"]) <= set(slate["game_id"])
    # Paper: priced and graded, but no money follows.
    assert (bp_rows["stake"] == 0).all(), bp_rows[["book", "market", "stake"]]


def test_bp_board_past_the_age_gate_is_refused_not_silently_dropped(tmp_path: Path) -> None:
    result = _run(tmp_path, "--bp-max-age-min", "1")
    assert result.returncode == 0, result.stderr
    assert "board refused" in result.stdout, result.stdout
    slate = _slate(tmp_path)
    assert slate[slate["book"].astype(str).str.startswith("bp:")].empty


def test_bp_stake_flag_lets_the_board_carry_money(tmp_path: Path) -> None:
    result = _run(tmp_path, "--bp-max-age-min", "100000", "--bp-stake")
    assert result.returncode == 0, result.stderr
    assert "staked at zero" not in result.stdout.split("bettingpros:")[-1].split("\n")[0]
    slate = _slate(tmp_path)
    bp_rows = slate[slate["book"].astype(str).str.startswith("bp:")]
    assert not bp_rows.empty, result.stdout
