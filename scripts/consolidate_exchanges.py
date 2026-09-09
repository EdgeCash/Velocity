"""Merge exchange snapshot parquets into rolling consolidated files (durability).

GitHub Actions artifacts expire, and exchange data — unlike The Odds API —
cannot be re-pulled once gone (docs/BUILD_EXCHANGES.md E2: durability is an
exit criterion, not a default). The weekly consolidation workflow downloads
the recent per-run artifacts plus the newest previous archive into one input
tree and runs this script: every ``*.parquet`` is grouped by kind (the
filename up to its timestamp tag, e.g. ``kalshi_lines``), concatenated,
de-duplicated row-wise, and rewritten as ``{kind}_consolidated.parquet`` — so
each weekly archive artifact carries the full normalized history to date.
Raw JSON is deliberately NOT rolled forward: it lives out its 90 days in the
per-run artifacts, which is the re-normalization window.

    python scripts/consolidate_exchanges.py --in downloaded --out consolidated
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

# kalshi_lines_20260909T001500Z.parquet -> kind "kalshi_lines";
# kalshi_lines_consolidated.parquet (a previous archive) -> the same kind.
_STAMPED = re.compile(r"^(?P<kind>.+)_\d{8}T\d{6}Z$")
_CONSOLIDATED = re.compile(r"^(?P<kind>.+)_consolidated$")


def kind_of(path: Path) -> str | None:
    """The consolidation group a parquet belongs to, or ``None`` to skip it."""
    stem = path.stem
    match = _STAMPED.match(stem) or _CONSOLIDATED.match(stem)
    return None if match is None else match["kind"]


def consolidate(in_dir: Path, out_dir: Path) -> dict[str, int]:
    """Merge every parquet under ``in_dir`` by kind; returns kind → row count."""
    groups: dict[str, list[Path]] = {}
    for path in sorted(in_dir.rglob("*.parquet")):
        kind = kind_of(path)
        if kind is not None:
            groups.setdefault(kind, []).append(path)

    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for kind, paths in sorted(groups.items()):
        frames = [pd.read_parquet(p) for p in paths]
        merged = pd.concat(frames, ignore_index=True)
        merged = merged.drop_duplicates().reset_index(drop=True)
        sort_cols = [c for c in ("snapshot", "timestamp", "line_id") if c in merged.columns]
        if sort_cols:
            merged = merged.sort_values(sort_cols).reset_index(drop=True)
        merged.to_parquet(out_dir / f"{kind}_consolidated.parquet", index=False)
        counts[kind] = len(merged)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Consolidate exchange snapshot parquets")
    parser.add_argument("--in", dest="in_dir", required=True, help="downloaded artifacts root")
    parser.add_argument("--out", dest="out_dir", required=True, help="consolidated output folder")
    args = parser.parse_args()

    counts = consolidate(Path(args.in_dir), Path(args.out_dir))
    if not counts:
        print("nothing to consolidate (no recognizable parquet files)")
    for kind, rows in counts.items():
        print(f"  {kind}: {rows} rows consolidated")


if __name__ == "__main__":
    main()
