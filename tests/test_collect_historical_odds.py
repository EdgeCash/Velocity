"""collect_historical_odds CLI — offline banking smoke.

The network fetch is credit-gated and lives behind the client's pragma; this
exercises the --from-file path: a banked raw snapshot is re-processed into the
raw/lines/events archive layout, marked closing. The normalizers themselves are
tested in test_ingest_theoddsapi*.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "collect_historical_odds.py"
SNAPSHOT = REPO / "tests" / "fixtures" / "theoddsapi_mlb.json"


def test_from_file_banks_the_archive(tmp_path: Path) -> None:
    out = tmp_path / "hist"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--from-file", str(SNAPSHOT),
         "--snapshot", "2026-07-22T23:30:00Z", "--league", "mlb", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert "processed" in result.stdout
    # Raw JSON is banked verbatim, plus normalized lines + events parquet.
    assert list(out.glob("raw/hist_mlb_*.json"))
    lines_files = list(out.glob("lines_mlb_*.parquet"))
    assert lines_files and list(out.glob("events_mlb_*.parquet"))
    lines = pd.read_parquet(lines_files[0])
    assert (lines["is_closing"]).all()  # historical snapshots are the CLV anchor
    assert (lines["snapshot"] == "2026-07-22T23:30:00Z").all()
