"""Football props collector — offline banking smoke.

The network fetch is credit-gated and lives behind the client's pragma; this
exercises the ``--from-file`` path: banked per-event payloads are re-processed
into the raw/normalized archive layout. The normalizers themselves are tested
in test_props_slate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "collect_football_props.py"

_UPDATE = "2026-09-10T12:00:00Z"


def _per_event_payloads() -> dict:
    """A banked ``{event_id: payload}`` map, as the per-event endpoint returns."""
    event = {
        "id": "evt-001",
        "sport_key": "americanfootball_nfl",
        "commence_time": "2026-09-10T00:20:00Z",
        "home_team": "Kansas City Chiefs",
        "away_team": "Buffalo Bills",
        "bookmakers": [
            {
                "key": "draftkings",
                "last_update": _UPDATE,
                "markets": [
                    {
                        "key": "player_pass_yds",
                        "last_update": _UPDATE,
                        "outcomes": [
                            {"name": "Over", "description": "Josh Allen",
                             "price": 100, "point": 249.5},
                            {"name": "Under", "description": "Josh Allen",
                             "price": -120, "point": 249.5},
                        ],
                    }
                ],
            }
        ],
    }
    return {"evt-001": event}


def test_from_file_banks_raw_and_normalized(tmp_path: Path) -> None:
    payloads = tmp_path / "payloads.json"
    payloads.write_text(json.dumps(_per_event_payloads()))
    out = tmp_path / "props"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--from-file", str(payloads),
         "--league", "nfl", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert "processed" in result.stdout
    # Raw JSON banked verbatim, plus the normalized PropLines parquet.
    assert list(out.glob("raw/nfl_event_*_evt-001.json"))
    props_files = list(out.glob("props_nfl_*.parquet"))
    assert props_files
    props = pd.read_parquet(props_files[0])
    assert len(props) == 2  # over + under
    assert (props["market"] == "pass_yards").all()
    assert (props["league"] == "nfl").all()
    assert not props["is_closing"].any()  # a live snapshot, not the close


def test_from_file_empty_board_succeeds(tmp_path: Path) -> None:
    payloads = tmp_path / "payloads.json"
    payloads.write_text(json.dumps({}))
    out = tmp_path / "props"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--from-file", str(payloads),
         "--league", "ncaaf", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    props_files = list(out.glob("props_ncaaf_*.parquet"))
    assert props_files
    assert pd.read_parquet(props_files[0]).empty
