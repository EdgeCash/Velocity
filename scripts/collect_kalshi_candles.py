"""Bank Kalshi candlesticks for recently settled markets — the exchange CLV archive.

For every game-series market settled since the last run this pulls two candle
sets (docs/BUILD_EXCHANGES.md E2): 1-minute candles for the final pre-close
window (the honest close lives there — ``pit.closing_line`` later keeps the
last pre-kickoff observation) and hourly candles for the market's whole life
(line-movement history). Raw payloads are banked verbatim per market plus one
normalized ``Lines`` parquet of the 1-minute series, ``is_closing=True``.

Kalshi's live endpoints keep only ~3 months of candles, so this must run on a
schedule — the ``/historical`` archive does carry pre-cutoff seasons (probe
2026-09-09), but bank-forward is still the primary posture. Keyless; paced.

    python scripts/collect_kalshi_candles.py --out artifacts/exchanges
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.kalshi import (
    GAME_MARKET_BY_SERIES,
    KalshiClient,
    normalize_kalshi_candles,
    parse_market_ticker,
)

SERIES_BY_LEAGUE = {
    "nfl": ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL", "KXNFLTEAMTOTAL"),
    "ncaaf": ("KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNCAAFTEAMTOTAL"),
}
# 1-minute window before close: kickoff is unknown at collect time, and close
# lands ~3h after kickoff plus settlement — 26h covers any same-day start.
MINUTE_WINDOW_S = 26 * 3600


def _unix(value: str) -> int | None:
    stamp = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(stamp) else int(stamp.timestamp())


def collect(
    leagues: tuple[str, ...],
    since_hours: int,
    collected_at: pd.Timestamp,
    out_raw: Path,
    sleep_seconds: float,
    max_markets: int = 0,
) -> pd.DataFrame:
    """Candles for markets settled in the trailing window → one Lines frame."""
    client = KalshiClient()
    tag = collected_at.strftime("%Y%m%dT%H%M%SZ")
    min_close = int(collected_at.tz_localize("UTC").timestamp()) - since_hours * 3600
    frames: list[pd.DataFrame] = []
    pulled = 0

    for league in leagues:
        for series in SERIES_BY_LEAGUE.get(league, ()):
            settled = client.markets(series, status="settled", min_close_ts=min_close)
            markets = settled["markets"]
            print(f"  {league} {series}: {len(markets)} settled markets in window")
            for market in markets:
                if max_markets and pulled >= max_markets:
                    print(f"  stopping at --max-markets {max_markets}")
                    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                pulled += 1
                ticker = str(market.get("ticker", ""))
                parsed = parse_market_ticker(ticker)
                if parsed is None or GAME_MARKET_BY_SERIES.get(parsed.series) is None:
                    continue
                close_ts = _unix(str(market.get("close_time", "")))
                open_ts = _unix(str(market.get("open_time", "")))
                if close_ts is None or open_ts is None:
                    continue
                minute = client.candlesticks(
                    series, ticker, max(open_ts, close_ts - MINUTE_WINDOW_S), close_ts, 1
                )
                time.sleep(sleep_seconds)
                hourly = client.candlesticks(series, ticker, open_ts, close_ts, 60)
                time.sleep(sleep_seconds)
                (out_raw / f"kalshi_candles_{ticker}_{tag}.json").write_text(
                    json.dumps({"market": market, "minute": minute, "hourly": hourly})
                )
                lines = normalize_kalshi_candles(market, minute, is_closing=True)
                frames.append(lines.assign(league=league, collected_at=collected_at, snapshot=tag))

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank Kalshi candles for settled markets")
    parser.add_argument("--out", default="artifacts/exchanges", help="output folder (private)")
    parser.add_argument("--leagues", nargs="+", default=list(SERIES_BY_LEAGUE))
    parser.add_argument(
        "--since-hours", type=int, default=36, help="settled-market lookback window"
    )
    parser.add_argument("--sleep", type=float, default=0.15, help="pause between candle calls")
    parser.add_argument(
        "--max-markets", type=int, default=0, help="stop after N markets (0 = no cap)"
    )
    args = parser.parse_args()

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    out = Path(args.out)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    print(f"Kalshi candle bank @ {now.isoformat()} (settled within {args.since_hours}h)")
    lines = collect(
        tuple(args.leagues), args.since_hours, stamp, raw, args.sleep, args.max_markets
    )

    dest = out / f"kalshi_candle_lines_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
    lines.to_parquet(dest, index=False)
    print(f"wrote {len(lines)} candle-close rows to {dest}")
    if lines.empty:
        print("note: nothing settled in the window (quiet day)")


if __name__ == "__main__":
    main()
