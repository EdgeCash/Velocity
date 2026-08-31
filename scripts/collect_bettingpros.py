"""Snapshot live BettingPros game lines to a private parquet (the collector).

BettingPros has **no historical archive** — a line only exists while it's live —
so building line history means snapshotting the current board on a schedule. This
script takes one snapshot of the three game markets (spread / total / moneyline)
for both leagues and writes a single timestamped parquet, then snapshots the
NFL ``/props`` board (BettingPros prop projections + EV; premium fields need
the ``BP_USER_ID``/``BP_USER_KEY`` pair, and the endpoint has no NCAAF).

It is designed to run as a **GitHub Actions** job (where the ``BP_*`` secrets
live) and to upload its output as a **private Actions artifact**. It deliberately
does *not* commit anything: the repo is public and paid line data must never land
in it (provider ToS, and it would leak our edge).

Credentials come from the environment only — ``BP_API_KEY`` and the optional
``BP_USER_ID`` / ``BP_USER_KEY`` premium pair — never a literal.

    BP_API_KEY=... python scripts/collect_bettingpros.py --out artifacts/bp
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.bettingpros import BettingProsClient, normalize_props

SPORTS = ("NFL", "NCAAF")


def _retry_5xx(fn, label: str):  # type: ignore[no-untyped-def]
    """Call ``fn``, retrying HTTP 5xx twice with backoff (0/15/45s).

    BettingPros' gateway has been throwing intermittent 504s (Aug 2026);
    without a retry one blip kills the whole 3-hourly snapshot. Call-budget
    math: the cap is 5k/day and a full run spends ~8 calls, so even every
    call 5xx-ing into both retries all day stays under ~200 calls/day (~4%
    of cap). 4xx (auth/quota/throttle) is NOT retried here — burning more
    calls into a quota error is exactly what the budget must never do.
    """
    for attempt, delay in enumerate((0, 15, 45)):
        if delay:
            print(f"  {label}: gateway error; retrying in {delay}s")
            time.sleep(delay)
        try:
            return fn()
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == 2:
                raise
    raise RuntimeError("unreachable")
# The /props endpoint serves NFL/NBA/MLB/NHL only (the spec's prop sport
# enum has no NCAAF) — college props stay model-generated.
PROP_SPORTS = ("NFL",)


def collect(
    sports: tuple[str, ...], collected_at: pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """One canonical ``Lines`` frame + the events map for ``sports``.

    The events frame (``game_id``/``home_team``/``away_team``/``kickoff``)
    is what lets a grader bridge our Odds-API-keyed bets to these BP
    snapshots by team names — the missing link for CLV-vs-close.
    """
    client = BettingProsClient.from_env()
    frames: list[pd.DataFrame] = []
    event_rows: list[dict[str, object]] = []
    failed: list[str] = []
    for sport in sports:
        try:
            events = _retry_5xx(lambda s=sport: client.events(s), f"{sport} events")
        except urllib.error.HTTPError as exc:
            # A sustained outage on ONE sport's route (seen live: NCAAF 504s
            # outlasting the 60s of backoff while NFL served fine) must not
            # void the other sport's already-fetched lines. Bank what
            # succeeded; the 3-hourly schedule retries the rest.
            print(f"  {sport}: skipped after retries ({exc})")
            failed.append(sport)
            continue
        for e in events:
            participants = e.get("participants") or []
            names = [p.get("name") for p in participants if isinstance(p, dict)]
            event_rows.append({
                "game_id": str(e.get("id")),
                "home_team": e.get("home") or (names[1] if len(names) > 1 else None),
                "away_team": e.get("visitor") or (names[0] if names else None),
                "kickoff": e.get("scheduled"),
                "league": sport.lower(),
            })
        try:
            lines = _retry_5xx(
                lambda s=sport, ev=events: client.game_lines(
                    s, event_ids=[e["id"] for e in ev]
                ),
                f"{sport} lines",
            )
        except urllib.error.HTTPError as exc:
            print(f"  {sport}: skipped after retries ({exc})")
            failed.append(sport)
            continue
        lines = lines.assign(league=sport.lower(), collected_at=collected_at)
        frames.append(lines)
        print(
            f"  {sport}: {len(lines)} lines "
            f"({lines['game_id'].nunique()} games, {lines['book'].nunique()} books)"
        )
    lines_out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    events_out = pd.DataFrame(
        event_rows, columns=["game_id", "home_team", "away_team", "kickoff", "league"]
    ).assign(collected_at=collected_at)
    return lines_out, events_out, failed


def probe_props() -> None:
    """Status-code probe of ``/props`` across sports — settles provisioning.

    MLB runs midsummer, so a live board definitely exists for it: an MLB 200
    (even with few props) alongside football 429s would mean the route works
    and football is seasonal; 429 on every sport — in-season and off — means
    the partner key has no ``/props`` provisioning at all. limit=1, one
    request per sport, spaced under the 5 RPS budget; nothing is banked.
    """
    client = BettingProsClient.from_env()
    for sport in ("NFL", "MLB", "NBA", "NHL"):
        time.sleep(3)
        try:
            payload = client.props(sport, limit=1)
            n = len(payload.get("props") or [])
            print(f"  {sport}: HTTP 200 — {n} prop(s) returned")
        except urllib.error.HTTPError as exc:
            print(f"  {sport}: HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001 - a probe reports, never raises
            print(f"  {sport}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot BettingPros game lines")
    parser.add_argument("--out", default="artifacts/bp", help="output folder (private, not git)")
    parser.add_argument(
        "--sports", nargs="+", default=list(SPORTS), help="sports to snapshot (default NFL NCAAF)"
    )
    parser.add_argument(
        "--probe-props", action="store_true",
        help="probe /props status codes across sports and exit (no snapshot)",
    )
    args = parser.parse_args()

    if args.probe_props:
        print("probing /props availability per sport")
        probe_props()
        return

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    print(f"BettingPros snapshot @ {now.isoformat()}")
    df, events, failed = collect(tuple(args.sports), stamp)
    if failed and len(failed) == len(args.sports):
        # Every sport failed after retries — a real outage worth a red run.
        raise SystemExit(f"all sports failed after retries: {failed}")
    if failed:
        print(f"note: partial snapshot — {failed} skipped after retries; "
              "the next scheduled run picks them back up")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag_now = now.strftime("%Y%m%dT%H%M%SZ")
    dest = out / f"bp_lines_{tag_now}.parquet"
    df.to_parquet(dest, index=False)
    print(f"wrote {len(df)} rows to {dest}")
    # The events map — game_id ↔ team names ↔ kickoff — is what a grader
    # needs to bridge Odds-API-keyed bets onto these snapshots for CLV.
    events.to_parquet(out / f"bp_events_{tag_now}.parquet", index=False)
    print(f"wrote {len(events)} event rows")
    if df.empty:
        # Off-season / no board yet is not an error — the job still succeeds so the
        # schedule keeps running; the artifact just carries an empty frame.
        print("note: no live lines right now (off-season or no board posted yet)")

    # Prop board snapshot — the BettingPros projections + EV per prop (premium
    # fields null on free-tier credentials). Raw payload banked verbatim,
    # normalized frame alongside the lines parquet. Best-effort: prop trouble
    # never sinks the lines snapshot above. The game-lines batch just spent
    # several requests in quick succession, so a 429 here is the partner
    # throttle (5 RPS) still cooling — proven on the first live dispatch —
    # hence the backoff retries.
    client = BettingProsClient.from_env()
    tag = now.strftime("%Y%m%dT%H%M%SZ")
    for sport in PROP_SPORTS:
        try:
            payload = None
            for attempt, delay in enumerate((0, 15, 30)):
                if delay:
                    print(f"  {sport} props throttled (429); retrying in {delay}s")
                    time.sleep(delay)
                try:
                    payload = client.props(sport)
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code != 429 or attempt == 2:
                        raise
            assert payload is not None
            raw_dir = out / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"props_{sport.lower()}_{tag}.json").write_text(json.dumps(payload))
            # The /markets listing supplies the durable market_slug per id —
            # what the intel layer keys its slug→market table on. Best-effort:
            # a failed listing just leaves the column empty (abstain, never
            # guess).
            try:
                slugs = {int(m["id"]): str(m.get("slug") or "")
                         for m in client.markets(sport) if m.get("id") is not None}
            except Exception as exc:  # noqa: BLE001 - slug metadata is additive
                print(f"  {sport} market slugs skipped ({exc})")
                slugs = {}
            props = normalize_props(payload, slugs).assign(
                league=sport.lower(), collected_at=stamp
            )
            dest = out / f"bp_props_{sport.lower()}_{tag}.parquet"
            props.to_parquet(dest, index=False)
            n_proj = int(props["projection"].notna().sum()) if not props.empty else 0
            print(f"  {sport} props: {len(props)} rows "
                  f"({n_proj} with projections{'* premium' if n_proj else ''}) → {dest}")
        except Exception as exc:  # noqa: BLE001 - props are additive, lines already saved
            print(f"  {sport} props skipped ({exc})")


if __name__ == "__main__":
    main()
