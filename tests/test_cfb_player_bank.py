"""The coverage gate on the college player bank — it refuses, it no longer prints.

cfbfastR fills a season progressively, and a season it has not finished
attributing does not look broken downstream: it looks like a season whose
quarterbacks threw no touchdowns, which the DFS projection prices with a
straight face. The build measured that and printed "TOO THIN to price a
lineup" — then banked the season anyway. These pin the refusal.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location(
    "build_cfb_player_games", REPO / "scripts" / "build_cfb_player_games.py"
)
bank = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(bank)


def _season(season: int, rows: int) -> pd.DataFrame:
    return pd.DataFrame({
        "season": [season] * rows,
        "week": list(range(1, rows + 1)),
        "game_id": [f"g{i}" for i in range(rows)],
        "player_id": [f"p{i}" for i in range(rows)],
        "pass_tds": [1.0] * rows,
    })


def _fetcher(
    monkeypatch: pytest.MonkeyPatch,
    by_season: dict[int, tuple[pd.DataFrame, float, list[int]]],
) -> None:
    import velocity.ingest.cfb_players as cfb

    monkeypatch.setattr(cfb, "fetch_player_games", lambda s: by_season[s])


def test_a_season_below_the_coverage_bar_is_not_banked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "player_games.parquet"
    _season(2025, 3).to_parquet(out, index=False)
    _fetcher(monkeypatch, {2026: (_season(2026, 5), 0.21, [])})

    bank.bank_player_games([2026], out)

    banked = pd.read_parquet(out)
    # Untouched: the good season is still there and the thin one never landed.
    assert sorted(banked["season"].unique()) == [2025]


def test_a_season_that_clears_the_bar_replaces_what_was_banked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "player_games.parquet"
    pd.concat([_season(2025, 3), _season(2026, 1)]).to_parquet(out, index=False)
    _fetcher(monkeypatch, {2026: (_season(2026, 5), 0.62, [])})

    bank.bank_player_games([2026], out)

    banked = pd.read_parquet(out)
    assert sorted(banked["season"].unique()) == [2025, 2026]
    assert int((banked["season"] == 2026).sum()) == 5  # the stale row is gone
    assert int((banked["season"] == 2025).sum()) == 3


def test_a_refused_season_keeps_the_weeks_already_banked_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bad fetch must not throw away five good weeks — out loud, though.

    Rows nobody refreshed are exactly the kind that keep pricing while looking
    healthy, so the run names how many it is holding rather than leaving the
    season's silence to be read as success.
    """
    out = tmp_path / "player_games.parquet"
    _season(2026, 4).to_parquet(out, index=False)
    _fetcher(monkeypatch, {2026: (_season(2026, 9), 0.30, [])})

    bank.bank_player_games([2026], out)

    banked = pd.read_parquet(out)
    assert int((banked["season"] == 2026).sum()) == 4
    printed = capsys.readouterr().out
    assert "TOO THIN" in printed
    assert "4 already-banked 2026 player-games kept" in printed


def test_an_unreachable_season_never_blocks_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "player_games.parquet"
    _season(2024, 2).to_parquet(out, index=False)

    import velocity.ingest.cfb_players as cfb

    def fetch(season: int) -> tuple[pd.DataFrame, float, list[int]]:
        if season == 2025:
            raise RuntimeError("404")
        return _season(season, 6), 0.8, []

    monkeypatch.setattr(cfb, "fetch_player_games", fetch)
    bank.bank_player_games([2025, 2026], out)

    banked = pd.read_parquet(out)
    assert sorted(banked["season"].unique()) == [2024, 2026]
