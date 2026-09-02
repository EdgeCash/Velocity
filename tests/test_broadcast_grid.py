"""The weekend broadcast grid — packing, fact strips, media rows, rendering.

Pins the pure pieces exactly: lane packing splits only genuinely concurrent
games on one network, the fact strip states the posted favorite and total
with no verdict, the media map prefers TV outlets, kickoffs convert to
Eastern, and the renderer produces a real PNG from synthetic blocks.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
from velocity.report.broadcast_grid import (
    GridGame,
    consensus_line_text,
    eastern,
    normalize_media,
    pack_lanes,
    render_grid,
    window_label,
)

_SCRIPT = Path(__file__).parent.parent / "scripts" / "render_broadcast_grid.py"
spec = importlib.util.spec_from_file_location("render_broadcast_grid", _SCRIPT)
assert spec is not None and spec.loader is not None
rbg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rbg)


def _game(row: str, hour: float, away: str = "AWY", home: str = "HOM") -> GridGame:
    base = pd.Timestamp("2026-09-05 00:00")
    return GridGame(row=row, away=away, home=home,
                    kickoff_et=base + pd.Timedelta(hours=hour))


def test_pack_lanes_splits_only_concurrent_games() -> None:
    packed = pack_lanes([
        _game("ABC", 12.0), _game("ABC", 15.5), _game("ABC", 19.5),  # sequential
        _game("ESPN", 15.5, "A1"), _game("ESPN", 15.5, "A2"),  # regional split
        _game("FOX", 11.0),
    ], duration_hours=3.5)
    rows = dict(packed)
    # Broadcast prominence first (the reference grammar), then first kickoff.
    assert [row for row, _ in packed] == ["ABC", "FOX", "ESPN"]
    assert len(rows["ABC"]) == 1 and len(rows["ABC"][0]) == 3  # one lane, back-to-back
    assert len(rows["ESPN"]) == 2  # concurrent games each get a lane


def test_fact_strip_states_the_market_without_advice() -> None:
    assert consensus_line_text("UGA", "GT", 13.5, 51.5) == "UGA -13.5 · 51.5"
    assert consensus_line_text("UGA", "GT", -6.5, 44.0) == "GT -6.5 · 44"
    assert consensus_line_text("UGA", "GT", 0.0, 47.5) == "PK · 47.5"
    assert consensus_line_text("UGA", "GT", None, 47.5) == "47.5"
    assert consensus_line_text("UGA", "GT", float("nan"), None) == ""


def test_eastern_and_window_labels() -> None:
    # 17:00 UTC in September = 1:00 PM EDT — the early window.
    kick = eastern(pd.Timestamp("2026-09-13 17:00"))
    assert kick == pd.Timestamp("2026-09-13 13:00")
    assert window_label(kick) == "EARLY (1:00)"
    assert window_label(pd.Timestamp("2026-09-13 09:30")) == "MORNING"
    assert window_label(pd.Timestamp("2026-09-13 16:25")) == "LATE (4:05 / 4:25)"
    assert window_label(pd.Timestamp("2026-09-13 20:20")) == "PRIME TIME"


def test_normalize_media_prefers_tv_and_drops_partials() -> None:
    media = normalize_media([
        {"homeTeam": "LSU", "awayTeam": "Clemson", "outlet": "ESPN+",
         "mediaType": "web"},
        {"homeTeam": "LSU", "awayTeam": "Clemson", "outlet": "ABC",
         "mediaType": "tv"},
        {"homeTeam": "Tulsa", "awayTeam": "Oklahoma State", "outlet": None},
        "junk",
    ])
    assert media == {("LSU", "Clemson"): "ABC"}


def test_consensus_by_game_takes_the_newest_snapshot_median() -> None:
    games = pd.DataFrame([{"game_id": "g1", "home_team": "Tigers",
                           "away_team": "Bears", "kickoff": pd.Timestamp("2026-09-05")}])
    old, new = pd.Timestamp("2026-09-05 10:00"), pd.Timestamp("2026-09-05 12:00")
    rows = []
    for book, point, ts in (("a", -6.5, new), ("b", -7.5, new), ("c", -20.0, old)):
        rows.append({"game_id": "g1", "book": book, "market": "spread",
                     "side": "Tigers", "point": point, "price": -110,
                     "timestamp": ts})
    rows.append({"game_id": "g1", "book": "a", "market": "total", "side": "Over",
                 "point": 51.5, "price": -110, "timestamp": new})
    numbers = rbg.consensus_by_game(pd.DataFrame(rows), games)
    assert numbers["g1"]["spread_home"] == -7.0  # stale -20 snapshot ignored
    assert numbers["g1"]["total"] == 51.5


def test_render_grid_writes_a_png(tmp_path: Path) -> None:
    games = [
        GridGame("ABC", "ECU", "BAMA", pd.Timestamp("2026-09-05 12:00"),
                 "#592A8A", "#9E1B32", line_text="BAMA -21.5 · 52.5"),
        GridGame("ABC", "CLEM", "LSU", pd.Timestamp("2026-09-05 19:30"),
                 "#F56600", "#461D7C", line_text="LSU -10.5 · 51.5"),
        GridGame("FOX", "UNT", "IU", pd.Timestamp("2026-09-05 12:00"),
                 "#00853E", "#990000"),
    ]
    dest = render_grid(games, tmp_path / "grid.png",
                       title="NCAAF SATURDAY — SEP 5")
    assert dest.exists() and dest.stat().st_size > 20_000


def test_target_date_lands_on_the_league_day() -> None:
    wed = pd.Timestamp("2026-09-02")
    assert rbg.target_date("ncaaf", wed) == pd.Timestamp("2026-09-05")
    assert rbg.target_date("nfl", wed) == pd.Timestamp("2026-09-06")
    # The day itself counts — a Saturday render targets that Saturday.
    assert rbg.target_date("ncaaf", pd.Timestamp("2026-09-05 09:00")) \
        == pd.Timestamp("2026-09-05")


def test_render_grid_places_marks_when_available(tmp_path: Path) -> None:
    """Logos are a nicety: a real mark renders, a corrupt one degrades to the
    chip, and the league mark shifts the title without being required."""
    import matplotlib.pyplot as plt
    import numpy as np

    mark = tmp_path / "mark.png"
    plt.imsave(mark, np.ones((32, 32, 3)) * 0.5)
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not a png")

    games = [
        GridGame("ABC", "ECU", "BAMA", pd.Timestamp("2026-09-05 12:00"),
                 "#592A8A", "#9E1B32", line_text="BAMA -28.5 · 53.5",
                 away_logo=mark, home_logo=corrupt),
    ]
    dest = render_grid(games, tmp_path / "grid.png",
                       title="MARKS", league_logo=mark)
    assert dest.exists() and dest.stat().st_size > 10_000


def test_school_meta_carries_the_espn_id() -> None:
    from velocity.report.assets import parse_ncaaf_teams

    index = parse_ncaaf_teams([
        {"school": "Alabama", "abbreviation": "BAMA", "color": "#9E1B32",
         "id": 333},
        {"school": "Mystery U", "abbreviation": "MYS", "id": "junk"},
    ])
    assert index["Alabama"].espn_id == 333  # the ESPN CDN logo key
    assert index["Mystery U"].espn_id is None  # unparseable id, never a guess
