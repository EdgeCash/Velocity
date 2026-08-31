"""SP+ ratings pull — payload → frame normalization."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts" / "pull_cfbd_sp.py"
spec = importlib.util.spec_from_file_location("pull_cfbd_sp", _SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_sp_frame_normalizes_and_drops_average_rows() -> None:
    payloads = {
        2024: [
            {"team": "Georgia", "conference": "SEC", "rating": 27.3, "ranking": 2,
             "offense": {"rating": 34.1}, "defense": {"rating": 12.4},
             "specialTeams": {"rating": 0.6}},
            {"team": "nationalAverages", "rating": 0.0},  # dropped
            {"team": "Kent State", "conference": "MAC", "rating": -19.8,
             "ranking": 134, "offense": None, "defense": {"rating": 41.0}},
        ],
        2023: [
            {"team": "Georgia", "conference": "SEC", "rating": 30.1, "ranking": 1,
             "offense": {"rating": 35.0}, "defense": {"rating": 11.2},
             "specialTeams": {"rating": 1.1}},
        ],
    }
    frame = mod.sp_frame(payloads)
    assert len(frame) == 3
    assert list(frame["season"]) == [2023, 2024, 2024]  # sorted by season
    georgia = frame[(frame.season == 2024) & (frame.team == "Georgia")].iloc[0]
    assert georgia["rating"] == 27.3 and georgia["defense"] == 12.4
    kent = frame[frame.team == "Kent State"].iloc[0]
    assert kent["offense"] is None or kent["offense"] != kent["offense"]  # None/NaN ok
    assert "nationalAverages" not in set(frame["team"])
