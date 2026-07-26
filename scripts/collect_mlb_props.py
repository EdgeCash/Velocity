"""Snapshot MLB player-prop + derivative lines to a private archive (soft-market CLV).

The Odds API serves props AND the derivative markets (team totals, first-5-innings)
only from the per-event ``/events/{id}/odds`` endpoint. This pulls each event's board
once — asking for the prop and derivative markets together (credit-efficient) — then:

* banks the **raw** per-event JSON verbatim (so every market, including ones with no
  canonical normalizer yet, is preserved for the props/derivatives backtest),
* writes the normalized ``PropLines`` parquet for the prop markets we model, and
* writes the normalized derivative ``Lines`` parquet (F5 moneyline/run line/total +
  NRFI/YRFI) — the entry/closing archive the derivative CLV backtest replays.

Runs as a GitHub Action (where ``THE_ODDS_API`` lives) and uploads a PRIVATE artifact
— never commits, since the repo is public and paid odds must not land in it. Empty
(no board posted yet) is a success, not a failure.

    THE_ODDS_API=... python scripts/collect_mlb_props.py --out artifacts/mlb_props
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.theoddsapi import (
    DEFAULT_DERIVATIVE_MARKETS,
    DEFAULT_PROP_MARKETS,
    TheOddsAPIClient,
    events_of,
    normalize_derivative_markets,
    normalize_player_props,
)


def _combined_markets(props: str, derivatives: str) -> str:
    """Prop + derivative market keys as one comma list (both ride the per-event call)."""
    return ",".join(k for k in (props, derivatives) if k)


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot MLB prop + derivative lines")
    parser.add_argument("--out", default="artifacts/mlb_props", help="output folder (private)")
    parser.add_argument("--markets", default=DEFAULT_PROP_MARKETS, help="prop market keys")
    parser.add_argument("--derivative-markets", default=DEFAULT_DERIVATIVE_MARKETS,
                        help="team-total + first-5-innings keys (banked raw; empty to skip)")
    args = parser.parse_args()

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    tag = now.strftime("%Y%m%dT%H%M%SZ")
    markets = _combined_markets(args.markets, args.derivative_markets)
    print(f"MLB soft-market snapshot @ {now.isoformat()} — markets: {markets}")

    client = TheOddsAPIClient.from_env()
    payloads = client.event_odds_payloads("mlb", markets)

    out = Path(args.out)
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Bank the raw per-event JSON (all markets preserved for later normalization).
    for event_id, raw in payloads:
        (raw_dir / f"mlb_event_{tag}_{event_id}.json").write_text(json.dumps(raw))

    # Normalize both boards we model: the prop markets and the derivative game
    # markets (F5 + first inning). team_totals stays raw-banked only.
    frames = [normalize_player_props(events_of(raw), is_closing=False) for _, raw in payloads]
    props = (
        pd.concat(frames, ignore_index=True) if frames else normalize_player_props([])
    ).assign(league="mlb", collected_at=stamp)
    deriv_frames = [
        normalize_derivative_markets(events_of(raw), is_closing=False) for _, raw in payloads
    ]
    derivs = (
        pd.concat(deriv_frames, ignore_index=True)
        if deriv_frames
        else normalize_derivative_markets([])
    ).assign(league="mlb", collected_at=stamp)
    print(
        f"  {len(payloads)} events banked (raw); {len(props)} prop lines normalized "
        f"({props['game_id'].nunique()} games, {props['player'].nunique()} players); "
        f"{len(derivs)} derivative lines normalized"
    )

    dest = out / f"mlb_props_{tag}.parquet"
    props.to_parquet(dest, index=False)
    deriv_dest = out / f"mlb_derivatives_{tag}.parquet"
    derivs.to_parquet(deriv_dest, index=False)
    print(f"wrote {len(props)} prop rows to {dest}, {len(derivs)} derivative rows to "
          f"{deriv_dest}, and {len(payloads)} raw payloads to {raw_dir}")
    if client.remaining is not None:
        print(f"credits remaining this month: {client.remaining}")
    if not payloads:
        print("note: no events on the board right now (off-day or not yet posted)")


if __name__ == "__main__":
    main()
