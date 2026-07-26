"""collect_historical_props CLI — offline banking smoke.

The network fetch (historical events list + per-event odds) is credit-gated and lives
behind the client's pragma; this exercises the --from-file path: a banked raw
per-event payload is re-normalized into a PropLines parquet, marked closing. The
normalizers themselves are tested in test_props_slate / test_ingest_theoddsapi.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "collect_historical_props.py"
EVENT = REPO / "tests" / "fixtures" / "theoddsapi_mlb_props.json"


def test_from_file_banks_prop_parquet(tmp_path: Path) -> None:
    out = tmp_path / "hist_props"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--from-file", str(EVENT),
         "--snapshot", "2026-07-22T23:30:00Z", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert "prop lines" in result.stdout
    files = list(out.glob("props_mlb_*.parquet"))
    assert files
    props = pd.read_parquet(files[0])
    assert (props["is_closing"]).all()  # historical snapshots are the CLV anchor
    assert (props["snapshot"] == "2026-07-22T23:30:00Z").all()
    assert not props.empty  # the fixture carries prop lines
