"""Bank the sbro NCAAB closes: download, parse, join to games, commit-ready.

Free public archives (sportsbookreviewsonline), 2007-08 through 2021-22:
one xlsx per season (the final season is an HTML table). Output is the
``game_id``-keyed closes frame in hoopR home orientation
(:func:`velocity.ingest.sbro.join_sbro_closes`), the N3 backtest's market.

    python scripts/collect_sbro_ncaab.py --games datasets/ncaab/games.parquet \
        --out datasets/ncaab/closes.parquet --cache /tmp/sbro

``--cache`` keeps the raw downloads for reruns; already-cached seasons are
not re-fetched, which is also the polite way to hit the archive.
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

import pandas as pd
from velocity.ingest.sbro import (
    join_sbro_closes,
    normalize_sbro_season,
    parse_sbro_html,
    sbro_season_url,
)


def fetch_season(season: int, cache: Path) -> pd.DataFrame:
    """One season's raw wire frame, from cache or the archive."""
    url = sbro_season_url(season)
    suffix = ".html" if url.endswith("/") else ".xlsx"
    local = cache / f"ncaa-basketball-{season - 1}-{season % 100:02d}{suffix}"
    if not local.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            local.write_bytes(resp.read())
    if suffix == ".html":
        return parse_sbro_html(local.read_text(encoding="utf-8", errors="replace"))
    return pd.read_excel(local)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank sbro NCAAB closes")
    parser.add_argument("--start", type=int, default=2008)
    parser.add_argument("--end", type=int, default=2022,
                        help="last archive season (2022 = 2021-22, the final one)")
    parser.add_argument("--games", default="datasets/ncaab/games.parquet")
    parser.add_argument("--out", default="datasets/ncaab/closes.parquet")
    parser.add_argument("--cache", default="artifacts/sbro")
    args = parser.parse_args()

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    games = pd.read_parquet(args.games)

    frames = []
    for season in range(args.start, args.end + 1):
        try:
            raw = fetch_season(season, cache)
        except Exception as exc:  # noqa: BLE001 - a missing season is a gap
            print(f"{season}: fetch failed ({exc})")
            continue
        normalized = normalize_sbro_season(raw, season)
        frames.append(normalized)
        print(f"{season}: {len(normalized)} games parsed")
    closes = pd.concat(frames, ignore_index=True)
    joined = join_sbro_closes(closes, games)
    joined.to_parquet(args.out, index=False)
    per = joined.groupby("season").size()
    print(f"joined {len(joined)}/{len(closes)} games "
          f"({len(joined) / len(closes):.3f}) → {args.out}")
    print(per.to_string())


if __name__ == "__main__":
    main()
