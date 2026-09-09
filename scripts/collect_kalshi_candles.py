"""Bank Kalshi candlesticks for recently settled markets — the exchange CLV archive.

For every game-series market settled since the last run this pulls 1-minute
candles covering the window that brackets kickoff, which is where the honest
close lives: ``pit.closing_line`` later keeps the last observation strictly
before kickoff. Raw payloads are banked verbatim alongside one normalized
``Lines`` parquet, ``is_closing=True``.

**Batched, and it has to be.** Kalshi's batch candlestick endpoint caps a
request at 10,000 candles across all markets, so the batch size is computed
from the window — an 8-hour minute window is 480 candles a market, hence 20
markets a call. Pulling one market at a time is two orders of magnitude more
requests: a single college Saturday settles ~10,000 ladder rungs, which took
hours per-market and takes minutes batched.

Kalshi's live endpoints keep only ~3 months of candles, so this runs on a
schedule; the ``/historical`` archive carries older seasons (probe 2026-09-09)
but at hourly resolution only. Keyless.

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
# The exchange caps one request at this many candles across all its markets.
CANDLE_CAP = 10_000
# Pack to just under it: close times shift slightly between the listing and the
# candle service, and a single candle over the line fails the whole request.
_SAFE_CAP = int(CANDLE_CAP * 0.97)


def _unix(value: object) -> int | None:
    stamp = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(stamp) else int(stamp.timestamp())


def _batch_size(window_hours: float, period_interval: int) -> int:
    """Markets per request for a window of exactly ``window_hours`` (a guide)."""
    per_market = max(1, int(window_hours * 60 / max(period_interval, 1)))
    return max(1, CANDLE_CAP // per_market)


def pack_batches(
    markets: list[dict], window_hours: float, period_interval: int
) -> list[tuple[list[dict], int, int]]:
    """Group markets into requests that stay inside the candle cap.

    One request carries a single ``start_ts``/``end_ts``, so a batch spanning
    markets that closed hours apart asks for that whole span *per market* — a
    naive fixed-size batch of games from different kickoff slots breaches the
    cap and the exchange answers 400. Markets are therefore sorted by close
    time and packed greedily on the batch's real span, which keeps each
    request just under the limit.
    """
    ordered = sorted(markets, key=lambda m: _unix(m["close_time"]) or 0)
    window = int(window_hours * 3600)
    batches: list[tuple[list[dict], int, int]] = []
    current: list[dict] = []

    def span_candles(chunk: list[dict]) -> int:
        closes = [_unix(m["close_time"]) or 0 for m in chunk]
        seconds = (max(closes) - (min(closes) - window)) or 1
        # The exchange counts both endpoints, so a span of N intervals is N+1
        # candles; leaving that off overshoots the cap by a hair and the whole
        # request 400s.
        per_market = max(1, seconds // 60 // max(period_interval, 1)) + 1
        return int(per_market * len(chunk))

    for market in ordered:
        if current and span_candles([*current, market]) > _SAFE_CAP:
            closes = [_unix(m["close_time"]) or 0 for m in current]
            batches.append((current, min(closes) - window, max(closes)))
            current = []
        current.append(market)
    if current:
        closes = [_unix(m["close_time"]) or 0 for m in current]
        batches.append((current, min(closes) - window, max(closes)))
    return batches


def collect(
    leagues: tuple[str, ...],
    since_hours: int,
    collected_at: pd.Timestamp,
    out_raw: Path,
    sleep_seconds: float,
    window_hours: float,
    period_interval: int,
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
            markets = [
                m
                for m in settled["markets"]
                if (parsed := parse_market_ticker(str(m.get("ticker", ""))))
                and GAME_MARKET_BY_SERIES.get(parsed.series) is not None
                and _unix(m.get("close_time")) is not None
                # A voided or rescheduled duplicate never traded and has no
                # candles to bank (probe 2026-09-09) — skip before spending a
                # request slot on it.
                and float(m.get("volume_fp") or 0) > 0
            ]
            if max_markets:
                markets = markets[: max(0, max_markets - pulled)]
            pulled += len(markets)
            print(f"  {league} {series}: {len(markets)} settled markets with volume")

            by_ticker = {str(m["ticker"]): m for m in markets}
            for index, (chunk, start_ts, end_ts) in enumerate(
                pack_batches(markets, window_hours, period_interval)
            ):
                candles = client.candlesticks_batch(
                    [str(m["ticker"]) for m in chunk], start_ts, end_ts, period_interval
                )
                (out_raw / f"kalshi_candles_{series}_{index:05d}_{tag}.json").write_text(
                    json.dumps({"markets": chunk, "candles": candles})
                )
                for ticker, entries in candles.items():
                    market = by_ticker.get(ticker)
                    if market is None or not entries:
                        continue
                    lines = normalize_kalshi_candles(
                        market, {"candlesticks": entries}, is_closing=True
                    )
                    if not lines.empty:
                        frames.append(
                            lines.assign(
                                league=league, collected_at=collected_at, snapshot=tag
                            )
                        )
                time.sleep(sleep_seconds)
            if max_markets and pulled >= max_markets:
                print(f"  stopping at --max-markets {max_markets}")
                return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank Kalshi candles for settled markets")
    parser.add_argument("--out", default="artifacts/exchanges", help="output folder (private)")
    parser.add_argument("--leagues", nargs="+", default=list(SERIES_BY_LEAGUE))
    parser.add_argument(
        "--since-hours", type=int, default=36, help="settled-market lookback window"
    )
    parser.add_argument(
        "--window-hours",
        type=float,
        default=8.0,
        help="how far before a market's close to pull candles; must bracket kickoff, "
             "which sits roughly 3-4h before close for football",
    )
    parser.add_argument(
        "--period-interval", type=int, default=1, choices=(1, 60, 1440),
        help="candle width in minutes (1, 60 or 1440)",
    )
    parser.add_argument(
        "--sleep", type=float, default=0.6,
        help="pause between batch calls. Keyless read limits are undocumented and "
             "sustained candle pulls do hit 429 at a faster pace; a free API key "
             "would raise the ceiling if this ever needs to go quicker.",
    )
    parser.add_argument(
        "--max-markets", type=int, default=0, help="stop after N markets (0 = no cap)"
    )
    args = parser.parse_args()

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    out = Path(args.out)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    size = _batch_size(args.window_hours, args.period_interval)
    print(
        f"Kalshi candle bank @ {now.isoformat()} (settled within {args.since_hours}h, "
        f"{args.window_hours:g}h window at {args.period_interval}min → {size} markets/call)"
    )
    lines = collect(
        tuple(args.leagues), args.since_hours, stamp, raw, args.sleep,
        args.window_hours, args.period_interval, args.max_markets,
    )

    dest = out / f"kalshi_candle_lines_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
    lines.to_parquet(dest, index=False)
    print(f"wrote {len(lines)} candle-close rows to {dest}")
    if lines.empty:
        print("note: nothing settled in the window (quiet day)")


if __name__ == "__main__":
    main()
