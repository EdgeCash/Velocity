"""A range backfill replaces only the seasons it fetched — never the file."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

_SPEC = importlib.util.spec_from_file_location(
    "build_ncaaf_pbp",
    Path(__file__).resolve().parents[1] / "scripts" / "build_ncaaf_pbp_datasets.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def _plays(season: int, n: int, tag: str) -> pd.DataFrame:
    return pd.DataFrame({
        "play_id": [f"{tag}{i}" for i in range(n)],
        "game_id": [f"g{season}"] * n,
        "season": [season] * n,
        "week": [1] * n,
        "posteam": ["A"] * n,
        "defteam": ["B"] * n,
        "epa": [0.1] * n,
    })


def test_backfilling_one_season_keeps_every_other_season() -> None:
    existing = pd.concat([_plays(2023, 3, "x"), _plays(2024, 4, "y"), _plays(2026, 2, "z")])
    fetched = _plays(2025, 5, "new")
    out = _MOD.merge_seasons(existing, fetched, 2025, 2025)
    assert out.groupby("season").size().to_dict() == {2023: 3, 2024: 4, 2025: 5, 2026: 2}


def test_a_refetched_season_is_replaced_not_duplicated() -> None:
    existing = pd.concat([_plays(2023, 3, "x"), _plays(2024, 4, "old")])
    fetched = _plays(2024, 6, "fresh")
    out = _MOD.merge_seasons(existing, fetched, 2024, 2024)
    assert out.groupby("season").size().to_dict() == {2023: 3, 2024: 6}
    assert not out["play_id"].str.startswith("old").any()


def test_no_existing_file_writes_the_fetch() -> None:
    fetched = _plays(2025, 2, "n")
    assert len(_MOD.merge_seasons(None, fetched, 2025, 2025)) == 2
    assert len(_MOD.merge_seasons(fetched.iloc[0:0], fetched, 2025, 2025)) == 2
