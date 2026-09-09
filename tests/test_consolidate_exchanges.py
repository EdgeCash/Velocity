"""Exchange archive consolidation — parquet merging is lossless and idempotent."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from consolidate_exchanges import consolidate, kind_of  # noqa: E402


def _frame(snapshot: str, prices: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "line_id": [f"g|moneyline|nyg|kalshi|{i}" for i in range(len(prices))],
            "price": prices,
            "snapshot": snapshot,
        }
    )


def test_kind_of_recognizes_stamped_and_consolidated_names() -> None:
    assert kind_of(Path("kalshi_lines_20260909T001500Z.parquet")) == "kalshi_lines"
    assert kind_of(Path("kalshi_candle_lines_20260910T120000Z.parquet")) == "kalshi_candle_lines"
    assert kind_of(Path("kalshi_lines_consolidated.parquet")) == "kalshi_lines"
    assert kind_of(Path("notes.parquet")) is None


def test_consolidate_merges_dedupes_and_rolls_forward(tmp_path: Path) -> None:
    in_dir = tmp_path / "downloaded"
    (in_dir / "run1").mkdir(parents=True)
    (in_dir / "run2").mkdir(parents=True)

    a = _frame("20260909T001000Z", [426, -456])
    b = _frame("20260909T011000Z", [430, -460])
    a.to_parquet(in_dir / "run1" / "kalshi_lines_20260909T001000Z.parquet", index=False)
    b.to_parquet(in_dir / "run2" / "kalshi_lines_20260909T011000Z.parquet", index=False)
    # A previous rolling archive overlapping run1 — the overlap must dedupe.
    prev = pd.concat([a], ignore_index=True)
    prev.to_parquet(in_dir / "run1" / "kalshi_lines_consolidated.parquet", index=False)

    out_dir = tmp_path / "consolidated"
    counts = consolidate(in_dir, out_dir)
    assert counts == {"kalshi_lines": 4}

    merged = pd.read_parquet(out_dir / "kalshi_lines_consolidated.parquet")
    assert len(merged) == 4  # 2 snapshots x 2 rows; the archive overlap deduped
    assert sorted(merged["snapshot"].unique()) == ["20260909T001000Z", "20260909T011000Z"]

    # Idempotence: consolidating the output again changes nothing.
    again = consolidate(out_dir, tmp_path / "twice")
    assert again == {"kalshi_lines": 4}
