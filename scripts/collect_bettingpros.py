"""Snapshot live BettingPros game lines to a private parquet (the collector).

BettingPros has **no historical archive** — a line only exists while it's live —
so building line history means snapshotting the current board on a schedule. This
script takes one snapshot of the three game markets (spread / total / moneyline)
for both leagues and writes a single timestamped parquet, then snapshots the
NFL ``/props`` board (BettingPros prop projections + EV; premium fields need
the ``BP_USER_ID``/``BP_USER_KEY`` pair, and the endpoint has no NCAAF).

It is designed to run as a **GitHub Actions** job (where the ``BP_*`` secrets
live) and to upload its output as an **Actions artifact**. It deliberately
does *not* commit anything: paid line data must never land in git, whose history
is permanent and clonable (provider ToS, and it would leak our edge).

Credentials come from the environment only — ``BP_API_KEY`` and the optional
``BP_USER_ID`` / ``BP_USER_KEY`` premium pair — never a literal.

    BP_API_KEY=... python scripts/collect_bettingpros.py --out artifacts/bp
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.bettingpros import (
    _PROP_COLUMNS,
    BettingProsClient,
    describe_payload_shape,
    describe_slug_coverage,
    normalize_props,
    pagination,
    payload_errors,
    prop_rows,
    scrub_secrets,
)

# The sports snapshotted by default. MLB joined on 2026-09-14: it is an
# in-season sport carrying the largest real exposure in the book
# (docs/WAGERING.md §1.3) and BettingPros quotes it, yet the collector had
# only ever asked for football — so the one league where a better number is
# worth the most was the one league with no second line feed at all.
#
# Call budget: a sport costs 3 calls for game lines (markets, events, offers)
# and a prop sport 2 more (props, markets). Three sports plus two prop sports
# is ~13 a run, 8 runs a day — about 2% of the 5k/day cap. The cap has never
# been the constraint here; asking for less than we pay for was.
SPORTS = ("NFL", "NCAAF", "MLB")


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
# Every league we price, because a probe finally asked.
#
# This list said ("NFL",) and then ("NFL", "MLB"), on a comment asserting "the
# /props endpoint serves NFL/NBA/MLB/NHL only (the spec's prop sport enum has
# no NCAAF)". **There is no such enum.** The published OpenAPI document types
# `sport` on /props as a free-form colon-delimited string, and the probe
# (--probe-props, run 2026-09-15) came back HTTP 200 with props on all six:
#
#   NFL 200 · NCAAF 200 · MLB 200 · WNBA 200 · NBA 200 · NHL 200
#
# So college props were model-only against a board that existed the whole
# time. NBA is the one omission on purpose: no NBA vertical prices anything,
# so banking it would be collecting for nobody — the exact habit
# docs/DATA_PROVIDERS.md now warns about.
#
# Budget: a sport costs ceil(rows/200) pages plus one /markets call. Five prop
# sports is roughly 30 calls a run, ~240 a day against a 5,000/day cap.
PROP_SPORTS = ("NFL", "NCAAF", "MLB", "WNBA", "NHL")

# The event fields the banked frame keeps. Everything else an event carries
# — lineups, park factors, notes, officials — is reported as unread until a
# normalizer is written against an observed payload.
_EVENT_COLUMNS = ("id", "home", "visitor", "scheduled", "participants")


def collect(
    sports: tuple[str, ...], collected_at: pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object], list[str]]:
    """One canonical ``Lines`` frame, the events map, and the raw event payloads.

    The events frame (``game_id``/``home_team``/``away_team``/``kickoff``)
    is what lets a grader bridge our Odds-API-keyed bets to these BP
    snapshots by team names — the missing link for CLV-vs-close.
    """
    client = BettingProsClient.from_env()
    frames: list[pd.DataFrame] = []
    event_rows: list[dict[str, object]] = []
    raw_events: dict[str, object] = {}
    failed: list[str] = []
    for sport in sports:
        try:
            payload = _retry_5xx(
                lambda s=sport: client.events_payload(s), f"{sport} events"
            )
            events = payload.get("events") or []
            raw_events[sport] = scrub_secrets(payload)
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
        # What an event carries beyond the four fields we keep. lineups and
        # park_factors default TRUE on this endpoint, so they have been arriving
        # all along; notes (weather, trends) and officials are now asked for.
        # Reported, not parsed: a normalizer written against an unseen shape is
        # the guess this repo keeps paying for.
        for line in describe_payload_shape(
            events, _EVENT_COLUMNS, f"{sport} event row"
        ):
            print(line)
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
    return lines_out, events_out, raw_events, failed


# One varied request each, against what production sends today. Ordered so the
# newest and most suspicious addition is tried first: include_correlated_picks
# arrived in #201, and NFL — the one league still served — is also the only one
# the flag was ever exercised against.
PROBE_VARIANTS: tuple[tuple[str, dict[str, object]], ...] = (
    ("as production sends it", {}),
    ("no include_correlated_picks", {"include_correlated_picks": None}),
    ("server default ev_threshold", {"ev_threshold": None}),
    ("include_markets=true", {"include_markets": "true"}),
    ("minimal (sport + limit only)", {
        "ev_threshold": None, "include_selections": None,
        "include_markets": None, "include_correlated_picks": None,
    }),
)


def _probe_once(client: BettingProsClient, sport: str, overrides: dict[str, object]) -> str:
    """One /props call, reported by what the rows ACTUALLY are.

    The previous probe printed ``len(payload["props"])``, which counts the
    error sentinel as a prop and renders an outage as "HTTP 200 — 1 prop(s)
    returned". Objects and sentinels are counted separately here for exactly
    that reason.
    """
    # A None override drops the parameter rather than sending "None".
    params = {k: v for k, v in overrides.items() if v is not None}
    drop = {k for k, v in overrides.items() if v is None}
    try:
        payload = client.props(sport, limit=25, **params)
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - a probe reports, never raises
        return f"{type(exc).__name__}: {exc}"

    if drop:
        # props() re-adds its defaults, so a "dropped" key is only really gone
        # if the echo agrees. Say so rather than claiming an untested variant.
        echoed = (payload.get("_parameters") or {}) if isinstance(payload, Mapping) else {}
        still = sorted(k for k in drop if echoed.get(k) not in (None, "", []))
        if still:
            return f"variant not applied (server still echoed {', '.join(still)})"

    rows = prop_rows(payload)
    bad = payload_errors(payload)
    good = len(rows) - bad
    meta = pagination(payload)
    total = meta.get("total_items", "?")
    if good:
        return f"{good} real row(s), {bad} sentinel(s), total_items {total}  <-- SERVED"
    return f"0 real rows, {bad} sentinel(s), total_items {total}"


def probe_props() -> None:
    """Why does /props serve NFL and answer every other sport with "error"?

    On 2026-09-16 the board returned HTTP 200, a healthy envelope and
    ``props: ["error"]`` for MLB, NCAAF, WNBA and NHL while NFL served 551 real
    objects — with ``total_items`` reading 2493 for MLB throughout, so nothing
    in the response said it had failed.

    This varies ONE parameter at a time against what production sends and
    reports whether the rows come back as objects or sentinels. NFL leads as
    the control: if NFL also stops being served by a variant, the variant is
    the answer for the wrong reason.

    A sport stops at its first served variant — that names the culprit and
    spends nothing further on it.
    """
    client = BettingProsClient.from_env()
    print("props probe — one varied request per (sport, variant), 25 rows each")
    print(f"  control first; {len(PROBE_VARIANTS)} variants, stopping at the first served\n")
    for sport in ("NFL", "MLB", "NCAAF", "WNBA", "NHL"):
        print(f"  {sport}")
        for label, overrides in PROBE_VARIANTS:
            time.sleep(3)  # under the 5 RPS budget
            result = _probe_once(client, sport, overrides)
            print(f"    {label:32s} {result}")
            if "SERVED" in result:
                break
        print()

def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot BettingPros game lines")
    parser.add_argument("--out", default="artifacts/bp", help="output folder (artifact, never git)")
    parser.add_argument(
        "--sports", nargs="+", default=list(SPORTS), help="sports to snapshot (default NFL NCAAF)"
    )
    parser.add_argument(
        "--max-prop-pages", type=int, default=20,
        help="page budget per sport's prop board (200 rows a page, server-capped)",
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
    df, events, raw_events, failed = collect(tuple(args.sports), stamp)
    if failed and len(failed) == len(args.sports):
        # Every sport failed after retries — a real outage worth a red run.
        raise SystemExit(f"all sports failed after retries: {failed}")
    if failed:
        print(f"note: partial snapshot — {failed} skipped after retries; "
              "the next scheduled run picks them back up")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag_now = now.strftime("%Y%m%dT%H%M%SZ")
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for sport, payload in raw_events.items():
        (raw_dir / f"events_{sport.lower()}_{tag_now}.json").write_text(
            json.dumps(payload)
        )
    dest = out / f"bp_lines_{tag_now}.parquet"
    df.to_parquet(dest, index=False)
    print(f"wrote {len(df)} rows to {dest}")
    # The events map — game_id ↔ team names ↔ kickoff — is what a grader
    # needs to bridge Odds-API-keyed bets onto these snapshots for CLV.
    events.to_parquet(out / f"bp_events_{tag_now}.parquet", index=False)
    print(f"wrote {len(events)} event rows")
    # The book id -> name listing. BettingPros identifies a book by a small
    # integer, and a board keyed "bp:10" is unreadable on a card and
    # un-auditable in the record. One extra call per run resolves every id to
    # a name; a failure just leaves the board keyed by id (abstain, never
    # guess), which is why this never raises.
    try:
        client_books = BettingProsClient.from_env().books(args.sports[0])
        if client_books:
            pd.DataFrame(
                [{"book_id": k, "book_name": v} for k, v in sorted(client_books.items())]
            ).assign(collected_at=stamp).to_parquet(
                out / f"bp_books_{tag_now}.parquet", index=False
            )
            print(f"wrote {len(client_books)} book names")
    except Exception as exc:  # noqa: BLE001 - labels are additive
        print(f"  book names skipped ({exc})")
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
                    # Every page, not just the first. The server caps a page at
                    # 200 and a real board runs to thousands, so the single call
                    # this replaced was banking a fifth of the NFL board and an
                    # eighth of the MLB one — with nothing saying so, because
                    # nothing read _pagination.
                    payload = client.props_all(sport, max_pages=args.max_prop_pages)
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code != 429 or attempt == 2:
                        raise
            assert payload is not None
            meta = pagination(payload)
            available = int(meta.get("total_pages", 1) or 1)
            collected = int(meta.get("pages_collected", 1) or 1)
            items = int(meta.get("items_collected", len(payload.get("props") or [])))
            print(f"  {sport} props: {collected} of {available} page(s), "
                  f"{items} of {meta.get('total_items', items)} rows")
            if collected < available:
                print(f"::warning title={sport} prop board truncated::"
                      f"collected {collected} of {available} pages "
                      f"(--max-prop-pages {args.max_prop_pages}); the banked board "
                      "is a sample, not the board")
            # HTTP 200 with a healthy envelope and props: ["error"] is how this
            # endpoint says it cannot serve a sport. Left alone it normalizes to
            # zero rows and prints as an empty board — so an outage and an
            # off-day read identically, which is the one shape this repo has
            # already been bitten by twice. Observed 2026-09-16 on MLB, NCAAF,
            # WNBA and NHL while NFL served 551 real rows.
            broken = payload_errors(payload)
            if broken:
                print(f"::error title={sport} prop board unavailable::"
                      f"BettingPros returned its error sentinel on {broken} of "
                      f"{collected} page(s). The envelope is healthy "
                      f"(total_items {meta.get('total_items', 0)}) and no prop "
                      "rows were served — an outage, not an empty board.")
            raw_dir = out / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            # scrub_secrets already ran per page inside props_all; this is the
            # belt to that braces. BettingPros echoes the full request URL —
            # partner key and user id included — in _pagination.self, and this
            # file is written to an artifact that outlives the run by a month.
            (raw_dir / f"props_{sport.lower()}_{tag}.json").write_text(
                json.dumps(scrub_secrets(payload))
            )
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
            # What this board actually serves, against what we map. The slug
            # table was written from reasoning and never confirmed against a
            # live snapshot (docs/INTEL.md §6), and an unmapped slug abstains
            # silently — so a board where most markets contribute nothing
            # reads exactly like a healthy one. Printing it every run makes
            # the gap impossible to miss and costs nothing.
            for line in describe_slug_coverage(props, sport):
                print(line)
            # And what the row carries that we never read. The OpenAPI document
            # types this array as [{}], so the only way to learn the shape is
            # to look at one. Keys, counts and value *shapes* — never values.
            for line in describe_payload_shape(
                payload.get("props") or [], _PROP_COLUMNS, f"{sport} prop row"
            ):
                print(line)
        except Exception as exc:  # noqa: BLE001 - props are additive, lines already saved
            print(f"  {sport} props skipped ({exc})")


if __name__ == "__main__":
    main()
