"""Snapshot NFL/NCAAF player-prop lines to a private archive (soft-market CLV).

The Odds API serves player props only from the per-event ``/events/{id}/odds``
endpoint. This pulls each event's prop board once per league, then:

* banks the **raw** per-event JSON verbatim (so every market, including ones
  with no canonical normalizer yet, is preserved for the prop backtest),
* writes the normalized :class:`~velocity.store.schema.PropLines` parquet for
  the prop markets we model (see ``PROP_MARKET_BY_KEY``), and
* writes the **team-total** lines (``team_totals`` rides the same per-event
  call for one extra market's credits) — their accumulated closes calibrate
  the ``min_team_total_disagreement`` gate.

Runs as a GitHub Action (where ``THE_ODDS_API`` lives) and uploads a PRIVATE
artifact — never commits, since the repo is public and paid odds must not land
in it. Empty (no board posted yet, or off-season) is a success, not a failure.

    THE_ODDS_API=... python scripts/collect_football_props.py --out artifacts/props

    # offline re-processing of banked raw payloads (also the test path):
    python scripts/collect_football_props.py --from-file payloads.json \
        --league nfl --out artifacts/props
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.theoddsapi import (
    DEFAULT_EVENT_MARKETS,
    events_of,
    normalize_odds_events,
    normalize_player_props,
)

_TEAM_TOTAL_MARKETS = ("team_total_home", "team_total_away")

# Per-league default market sets: the football leagues pull the six-market
# board + team totals; the other sports pull their HEADLINE prop
# (docs/PROPS.md) — the archive the per-sport prop models backtest on.
LEAGUE_PROP_MARKETS = {
    "nfl": DEFAULT_EVENT_MARKETS,
    "ncaaf": DEFAULT_EVENT_MARKETS,
    "mlb": "pitcher_strikeouts",
    "nhl": "player_shots_on_goal",
    "nba": "player_rebounds",
}


def _payloads_from_file(path: str) -> list[tuple[str, object]]:
    """Banked per-event payloads from disk: a ``{event_id: payload}`` map or a list."""
    payload = json.loads(Path(path).read_text())
    if isinstance(payload, dict):
        return [(str(event_id), raw) for event_id, raw in payload.items()]
    return [(str(events_of(raw)[0].get("id", i)) if events_of(raw) else str(i), raw)
            for i, raw in enumerate(payload)]


def snapshot_league(
    league: str,
    payloads: list[tuple[str, object]],
    out: Path,
    tag: str,
    collected_at: pd.Timestamp,
) -> pd.DataFrame:
    """Bank one league's raw payloads and write its normalized ``PropLines`` parquet."""
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for event_id, raw in payloads:
        (raw_dir / f"{league}_event_{tag}_{event_id}.json").write_text(json.dumps(raw))

    frames = [normalize_player_props(events_of(raw), is_closing=False) for _, raw in payloads]
    props = (
        pd.concat(frames, ignore_index=True) if frames else normalize_player_props([])
    ).assign(league=league, collected_at=collected_at)

    dest = out / f"props_{league}_{tag}.parquet"
    props.to_parquet(dest, index=False)
    n_games = props["game_id"].nunique() if not props.empty else 0
    n_players = props["player"].nunique() if not props.empty else 0
    print(
        f"  {league}: {len(payloads)} events banked (raw); {len(props)} prop lines "
        f"normalized ({n_games} games, {n_players} players) → {dest}"
    )

    # Team totals ride the same per-event payloads (DEFAULT_EVENT_MARKETS).
    # Banked as canonical Lines beside the props: their accumulated closes are
    # the calibration input the min_team_total_disagreement gate is waiting on
    # (docs/BACKTEST_NCAAF.md addendum). Empty is normal for boards that
    # haven't posted them or pre-cutover payloads.
    game_frames = [normalize_odds_events(events_of(raw), is_closing=False)
                   for _, raw in payloads]
    game_lines = pd.concat(game_frames, ignore_index=True) if game_frames else pd.DataFrame()
    if not game_lines.empty:
        team_totals = game_lines[game_lines["market"].isin(_TEAM_TOTAL_MARKETS)]
        if not team_totals.empty:
            tt_dest = out / f"team_totals_{league}_{tag}.parquet"
            team_totals.assign(league=league, collected_at=collected_at).to_parquet(
                tt_dest, index=False
            )
            print(f"  {league}: {len(team_totals)} team-total lines "
                  f"({team_totals['game_id'].nunique()} games) → {tt_dest}")
    return props


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot NFL/NCAAF player-prop lines")
    parser.add_argument("--out", default="artifacts/props", help="output folder (private)")
    parser.add_argument("--leagues", default="nfl ncaaf",
                        help="space-separated leagues to snapshot")
    parser.add_argument("--markets", default="",
                        help="per-event market keys (default: each league's "
                             "own set from LEAGUE_PROP_MARKETS)")
    parser.add_argument("--from-file",
                        help="saved per-event payloads JSON (offline; single league)")
    parser.add_argument("--league", default="nfl",
                        help="league label for --from-file payloads")
    args = parser.parse_args()

    now = datetime.now(UTC)
    collected_at = pd.Timestamp(now).tz_localize(None)
    tag = now.strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out)

    if args.from_file:
        payloads = _payloads_from_file(args.from_file)
        print(f"prop snapshot (from file) @ {now.isoformat()}")
        snapshot_league(args.league, payloads, out, tag, collected_at)
        print("processed 1 league from file")
        return

    from velocity.ingest.theoddsapi import TheOddsAPIClient  # network path

    client = TheOddsAPIClient.from_env()
    leagues = args.leagues.split()
    print(f"prop snapshot @ {now.isoformat()} — leagues: {leagues}")
    for league in leagues:
        markets = args.markets or LEAGUE_PROP_MARKETS.get(league, DEFAULT_EVENT_MARKETS)
        try:
            payloads = client.event_odds_payloads(league, markets)
        except Exception as exc:  # noqa: BLE001 - one league's board never blocks the other
            print(f"  {league}: fetch failed ({exc}); skipping")
            continue
        snapshot_league(league, payloads, out, tag, collected_at)
        if not payloads:
            print(f"  {league}: no events on the board (off-season or not yet posted)")
    if client.remaining is not None:
        print(f"credits remaining this month: {client.remaining}")


if __name__ == "__main__":
    main()
