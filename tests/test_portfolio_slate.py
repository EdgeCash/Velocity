"""Portfolio sizing in the live runner — the combined card, end-to-end offline."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "run_live_slate.py"
SNAPSHOT = REPO / "tests" / "fixtures" / "theoddsapi_nfl.json"


def _run(tmp_path: Path, *extra: str) -> tuple[subprocess.CompletedProcess, Path]:
    out = tmp_path / "slate"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--league", "nfl", "--data", "datasets/nfl",
         "--snapshot-file", str(SNAPSHOT), "--n-sims", "1000", "--max-days", "0",
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
