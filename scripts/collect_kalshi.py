"""Snapshot the Kalshi sports board — raw JSON + canonical parquet (free, keyless).

Kalshi's board is the exchange half of the closing-line story
(docs/BUILD_EXCHANGES.md E2): unlike The Odds API there is no vendor archive to
re-pull from beyond ~3 months, so the snapshots themselves are the record. Each
run banks BOTH the raw ``/markets`` payloads verbatim (so improved normalizers
can re-process later) and the normalized ``Lines``/``PropLines`` parquet, tagged
``snapshot``/``collected_at``/``league`` so ``backtest/archive.py`` splits
entry/close boards unchanged.

Runs as a GitHub Actions job and uploads to a private artifact (repo policy:
no odds data in git, docs/DATA_PROVIDERS.md); the consolidation workflow rolls
the parquet forward past artifact expiry. No secret needed — market data reads
are unauthenticated (verified live 2026-09-09).

    python scripts/collect_kalshi.py --out artifacts/exchanges
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.kalshi import (
    PROP_MARKET_BY_SERIES,
    KalshiClient,
    normalize_kalshi_markets,
    normalize_kalshi_props,
)

GAME_SERIES_BY_LEAGUE = {
    "nfl": ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL", "KXNFLTEAMTOTAL"),
    "ncaaf": ("KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNCAAFTEAMTOTAL"),
}
PROP_SERIES = tuple(PROP_MARKET_BY_SERIES)


def collect(
    leagues: tuple[str, ...], collected_at: pd.Timestamp, out_raw: Path
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Snapshot every mapped series; bank raw payloads; return (lines, props)."""
    client = KalshiClient()
    tag = collected_at.strftime("%Y%m%dT%H%M%SZ")
    line_frames: list[pd.DataFrame] = []
    prop_frames: list[pd.DataFrame] = []

    for league in leagues:
        for series in GAME_SERIES_BY_LEAGUE.get(league, ()):
            payload = client.markets(series)
            (out_raw / f"kalshi_{series}_{tag}.json").write_text(json.dumps(payload))
            lines = normalize_kalshi_markets(payload, collected_at)
            lines = lines.assign(league=league, collected_at=collected_at, snapshot=tag)
            line_frames.append(lines)
            print(f"  {league} {series}: {len(payload['markets'])} markets -> {len(lines)} lines")

    for series in PROP_SERIES:
        payload = client.markets(series)
        (out_raw / f"kalshi_{series}_{tag}.json").write_text(json.dumps(payload))
        props = normalize_kalshi_props(payload, collected_at)
        props = props.assign(league="nfl", collected_at=collected_at, snapshot=tag)
        prop_frames.append(props)
        print(f"  props {series}: {len(payload['markets'])} markets -> {len(props)} lines")

    lines_df = pd.concat(line_frames, ignore_index=True) if line_frames else pd.DataFrame()
    props_df = pd.concat(prop_frames, ignore_index=True) if prop_frames else pd.DataFrame()
    return lines_df, props_df


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot the Kalshi sports board")
    parser.add_argument("--out", default="artifacts/exchanges", help="output folder (private)")
    parser.add_argument(
        "--leagues", nargs="+", default=list(GAME_SERIES_BY_LEAGUE), help="leagues to snapshot"
    )
    args = parser.parse_args()

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    out = Path(args.out)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    print(f"Kalshi board snapshot @ {now.isoformat()}")
    lines, props = collect(tuple(args.leagues), stamp, raw)

    tag = now.strftime("%Y%m%dT%H%M%SZ")
    lines.to_parquet(out / f"kalshi_lines_{tag}.parquet", index=False)
    props.to_parquet(out / f"kalshi_props_{tag}.parquet", index=False)
    print(f"wrote {len(lines)} game lines, {len(props)} prop lines")
    if lines.empty and props.empty:
        print("note: empty board right now (off-season or between postings)")


if __name__ == "__main__":
    main()
