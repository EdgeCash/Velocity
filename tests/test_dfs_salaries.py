"""DK salary ingest — pure normalizers + the collector's offline path.

The draftables normalizer is pinned against a frozen payload in the real DK
shape: roster-slot duplicates dedupe to one row per player, a row without a
salary is dropped (never guessed), and the output validates the ``Salaries``
schema. The CLI's ``--from-file`` path is the same code the Action runs minus
the network fetch.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
from velocity.dfs.salaries import (
    Salaries,
    normalize_draft_groups,
    normalize_draftables,
)

REPO = Path(__file__).parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "dk_draftables.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text())


def test_normalize_draftables_validates_and_dedupes() -> None:
    out = normalize_draftables(_payload(), "12345")
    Salaries.validate(out)
    # Kelce appears twice (TE + FLEX roster slots) → one row; the null-salary
    # player is dropped → 2 rows total.
    assert len(out) == 2
    assert set(out["player_name"]) == {"Josh Allen", "Travis Kelce"}
    allen = out[out["player_name"] == "Josh Allen"].iloc[0]
    assert allen["salary"] == 8200
    assert allen["position"] == "QB"
    assert allen["team"] == "BUF"
    assert allen["competition"] == "BUF @ KC"
    assert allen["draft_group_id"] == "12345"
    # DK's offset timestamp lands as a naive UTC kickoff.
    assert pd.Timestamp(allen["kickoff"]) == pd.Timestamp("2026-09-11 00:20:00")


def test_normalize_draftables_empty_payload() -> None:
    out = normalize_draftables({}, "0")
    assert out.empty
    assert "salary" in out.columns


def test_normalize_draft_groups() -> None:
    lobby = {
        "DraftGroups": [
            {"DraftGroupId": 111, "ContestTypeId": 21, "StartDate": "2026-09-13T17:00:00Z",
             "GameCount": 13, "DraftGroupTag": "Featured"},
            {"DraftGroupId": None},  # malformed entry is skipped
        ]
    }
    groups = normalize_draft_groups(lobby)
    assert groups["draft_group_id"].tolist() == ["111"]
    assert groups.loc[0, "game_count"] == 13


def test_collector_from_file_banks_normalized_parquet(tmp_path: Path) -> None:
    out = tmp_path / "salaries"
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "collect_dk_salaries.py"),
         "--from-file", str(FIXTURE), "--draft-group", "12345",
         "--league", "nfl", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    files = list(out.glob("dk_salaries_nfl_*.parquet"))
    assert files
    salaries = pd.read_parquet(files[0])
    assert len(salaries) == 2
    assert (salaries["league"] == "nfl").all()
    assert "collected_at" in salaries.columns
