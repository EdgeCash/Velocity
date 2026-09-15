"""The Odds API ingest adapter — historical + live odds → canonical store.

The Odds API is the one paid feed with a real **historical archive**, so it is the
source for the closing-line snapshots that power CLV measurement and the
market-facing backtest (BettingPros, by contrast, is live-only). Both its live
``/odds`` and ``/historical/.../odds`` endpoints return the same event shape, so a
single pure normalizer serves both.

Two layers, kept strictly separate so the test gate stays offline:

* ``normalize_odds_events`` — **pure**: flattens the nested
  ``events → bookmakers → markets → outcomes`` JSON onto the canonical
  :class:`~velocity.store.schema.Lines` schema. Offline, deterministic, tested
  against frozen samples.
* :class:`TheOddsAPIClient` — the network layer. The key comes from the
  environment (``THE_ODDS_API``) only — never a literal — so the collector reads
  it from a GitHub Actions secret and the sandbox never sees it.

Convention notes:

* Markets. The three game markets map by key: ``h2h`` → ``moneyline``,
  ``spreads`` → ``spread``, ``totals`` → ``total``. Other market keys (props,
  alternates) are ignored — they don't fit the game-level Lines schema.
* Prices. Requested in ``american`` format, so ``price`` is already the American
  integer the schema wants. ``point`` is the outcome's number (null for
  moneyline, whose outcomes carry no point).
* ``game_id`` is The Odds API's own event id, which does not match our
  nflverse/CFBD ids; joining a snapshot to our games is done later by
  (teams, date). Credits: historical calls cost more than live — spend the
  100k/month budget deliberately.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from velocity.store.schema import Lines, PropLines

_BASE = "https://api.the-odds-api.com/v4"
_FETCH_TIMEOUT = 60

# The Odds API market key → canonical Lines market. Anything else is ignored.
# ``team_totals`` is resolved per-outcome into ``team_total_home`` /
# ``team_total_away`` using the event's own team names (the outcome's
# ``description`` names the team; ``name`` stays Over/Under).
GAME_MARKET_BY_KEY = {
    "h2h": "moneyline",
    "spreads": "spread",
    "totals": "total",
    "team_totals": "team_total",
}

# Friendly league → The Odds API sport key.
SPORT_KEYS = {
    "nfl": "americanfootball_nfl",
    "ncaaf": "americanfootball_ncaaf",
    "mlb": "baseball_mlb",
    "wnba": "basketball_wnba",
    "ncaab": "basketball_ncaab",
    "nhl": "icehockey_nhl",
}

# The Odds API sport key → friendly league. The league lives in the request
# *path*, never the query, so credit accounting reads it back from there.
LEAGUE_BY_SPORT_KEY = {v: k for k, v in SPORT_KEYS.items()}

# The Odds API player-prop market key → canonical prop stat (PropLines /
# the stats :mod:`velocity.models.props` simulates). Anything else is ignored.
PROP_MARKET_BY_KEY = {
    "player_pass_yds": "pass_yards",
    "player_pass_tds": "pass_tds",
    "player_rush_yds": "rush_yards",
    "player_reception_yds": "receiving_yards",
    "player_receptions": "receptions",
    "player_anytime_td": "anytime_td",
    "pitcher_strikeouts": "pitcher_strikeouts",
    "batter_home_runs": "batter_home_runs",
    "player_shots_on_goal": "shots_on_goal",
    "player_rebounds": "rebounds",
}
# The football six — the historical default; per-league prop pulls pass
# their own market subsets (docs/PROPS.md).
FOOTBALL_PROP_MARKETS = ",".join(list(PROP_MARKET_BY_KEY)[:6])
DEFAULT_PROP_MARKETS = FOOTBALL_PROP_MARKETS
# The per-event snapshot markets: props plus the team-total derivative. One
# /events/{id}/odds call carries them all, so team totals ride the prop
# collector for one extra market's worth of credits — and their banked closes
# are what calibrates the ``min_team_total_disagreement`` gate
# (docs/BACKTEST_NCAAF.md addendum).
DEFAULT_EVENT_MARKETS = DEFAULT_PROP_MARKETS + ",team_totals"

_PROP_SIDES = {"over": "over", "under": "under"}

_LINES_COLUMNS = [
    "line_id",
    "game_id",
    "book",
    "market",
    "side",
    "price",
    "point",
    "timestamp",
    "is_closing",
]


def _empty_lines() -> pd.DataFrame:
    empty = pd.DataFrame(
        {
            "line_id": pd.Series(dtype=str),
            "game_id": pd.Series(dtype=str),
            "book": pd.Series(dtype=str),
            "market": pd.Series(dtype=str),
            "side": pd.Series(dtype=str),
            "price": pd.Series(dtype="int64"),
            "point": pd.Series(dtype=float),
            "timestamp": pd.Series(dtype="datetime64[ns]"),
            "is_closing": pd.Series(dtype=bool),
        }
    )
    return Lines.validate(empty)


def normalize_odds_events(
    events: Iterable[Mapping[str, Any]],
    is_closing: bool = False,
    market_by_key: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Flatten a The Odds API events array onto the canonical ``Lines`` schema.

    ``events`` is the list of event objects (the top-level array of ``/odds`` or
    the ``data`` field of a historical response — use :func:`unwrap` for the
    latter). ``market_by_key`` selects which provider markets are kept and names
    them canonically — the three game markets by default. ``is_closing`` marks a
    snapshot taken at the close (the CLV anchor); live snapshots pass ``False``.
    """
    keep = GAME_MARKET_BY_KEY if market_by_key is None else market_by_key
    rows: list[dict[str, object]] = []
    for event in events:
        event_id = event.get("id")
        if event_id is None:
            continue
        for book in event.get("bookmakers") or []:
            book_key = book.get("key")
            book_update = book.get("last_update")
            for market in book.get("markets") or []:
                canonical = keep.get(str(market.get("key")))
                if canonical is None:
                    continue
                market_update = market.get("last_update") or book_update
                for outcome in market.get("outcomes") or []:
                    price = outcome.get("price")
                    if price is None:
                        continue
                    side = str(outcome.get("name", ""))
                    point = outcome.get("point")
                    market_name = canonical
                    if canonical == "team_total":
                        # Which team's total this is lives in the outcome's
                        # description; resolve against the event's own names.
                        # An unresolvable team is skipped, never guessed.
                        described = str(outcome.get("description", ""))
                        if described == str(event.get("home_team")):
                            market_name = "team_total_home"
                        elif described == str(event.get("away_team")):
                            market_name = "team_total_away"
                        else:
                            continue
                    rows.append(
                        {
                            "game_id": str(event_id),
                            "book": None if book_key is None else str(book_key),
                            "market": market_name,
                            "side": side,
                            "price": price,
                            # Moneyline outcomes carry no number.
                            "point": None if market_name.startswith("moneyline") else point,
                            "timestamp": market_update,
                            "is_closing": is_closing,
                        }
                    )
    if not rows:
        return _empty_lines()

    df = pd.DataFrame(rows)
    point_key = df["point"].map(lambda v: "" if pd.isna(v) else f"{float(v):g}")
    df["line_id"] = (
        df["game_id"]
        + "|" + df["market"]
        + "|" + df["side"].str.lower().str.replace(r"\s+", "-", regex=True)
        + "|" + df["book"].fillna("")
        + "|" + point_key
    )
    df["price"] = pd.to_numeric(df["price"], errors="coerce").round().astype("Int64")
    df["point"] = pd.to_numeric(df["point"], errors="coerce")
    ts = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df["timestamp"] = ts.dt.tz_localize(None)
    df = df.dropna(subset=["price"]).drop_duplicates("line_id").reset_index(drop=True)
    df["price"] = df["price"].astype(int)
    return Lines.validate(df[_LINES_COLUMNS])


_PROP_COLUMNS = [
    "line_id",
    "game_id",
    "book",
    "market",
    "player",
    "side",
    "price",
    "point",
    "timestamp",
    "is_closing",
]


def _empty_prop_lines() -> pd.DataFrame:
    empty = pd.DataFrame(
        {
            "line_id": pd.Series(dtype=str),
            "game_id": pd.Series(dtype=str),
            "book": pd.Series(dtype=str),
            "market": pd.Series(dtype=str),
            "player": pd.Series(dtype=str),
            "side": pd.Series(dtype=str),
            "price": pd.Series(dtype="int64"),
            "point": pd.Series(dtype=float),
            "timestamp": pd.Series(dtype="datetime64[ns]"),
            "is_closing": pd.Series(dtype=bool),
        }
    )
    return PropLines.validate(empty)


def normalize_player_props(
    events: Iterable[Mapping[str, Any]], is_closing: bool = False
) -> pd.DataFrame:
    """Flatten a The Odds API player-prop payload onto the canonical ``PropLines``.

    ``events`` are event-odds objects (from the per-event ``/events/{id}/odds``
    endpoint). Only the prop markets in :data:`PROP_MARKET_BY_KEY` are kept; each
    outcome's ``description`` is the player and ``name`` is ``Over``/``Under``.
    """
    rows: list[dict[str, object]] = []
    for event in events:
        event_id = event.get("id")
        if event_id is None:
            continue
        for book in event.get("bookmakers") or []:
            book_key = book.get("key")
            book_update = book.get("last_update")
            for market in book.get("markets") or []:
                stat = PROP_MARKET_BY_KEY.get(str(market.get("key")))
                if stat is None:
                    continue
                market_update = market.get("last_update") or book_update
                for outcome in market.get("outcomes") or []:
                    side = _PROP_SIDES.get(str(outcome.get("name", "")).strip().lower())
                    player = outcome.get("description")
                    price = outcome.get("price")
                    point = outcome.get("point")
                    if side is None or player is None or price is None or point is None:
                        continue
                    rows.append(
                        {
                            "game_id": str(event_id),
                            "book": None if book_key is None else str(book_key),
                            "market": stat,
                            "player": str(player),
                            "side": side,
                            "price": price,
                            "point": point,
                            "timestamp": market_update,
                            "is_closing": is_closing,
                        }
                    )
    if not rows:
        return _empty_prop_lines()

    df = pd.DataFrame(rows)
    player_key = df["player"].str.lower().str.replace(r"\s+", "-", regex=True)
    df["line_id"] = (
        df["game_id"]
        + "|" + df["market"]
        + "|" + player_key
        + "|" + df["side"]
        + "|" + df["book"].fillna("")
        + "|" + df["point"].map(lambda v: f"{float(v):g}")
    )
    df["price"] = pd.to_numeric(df["price"], errors="coerce").round().astype("Int64")
    df["point"] = pd.to_numeric(df["point"], errors="coerce")
    ts = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df["timestamp"] = ts.dt.tz_localize(None)
    df = df.dropna(subset=["price", "point"]).drop_duplicates("line_id").reset_index(drop=True)
    df["price"] = df["price"].astype(int)
    return PropLines.validate(df[_PROP_COLUMNS])


def extract_events(payload: Any) -> pd.DataFrame:
    """Pull per-event metadata (id, kickoff, teams) from an odds payload.

    Companion to :func:`normalize_odds_events`: the same ``/odds`` response that
    carries the lines also names each event's home/away team and commence time.
    Returns a frame ``[game_id, kickoff, home_team, away_team, sport_key]`` — the
    games side of a live slate, internally consistent with the lines (same event
    ids and provider team names).
    """
    rows: list[dict[str, object]] = []
    for event in unwrap(payload):
        event_id = event.get("id")
        if event_id is None:
            continue
        rows.append(
            {
                "game_id": str(event_id),
                "kickoff": event.get("commence_time"),
                "home_team": event.get("home_team"),
                "away_team": event.get("away_team"),
                "sport_key": event.get("sport_key"),
            }
        )
    df = pd.DataFrame(rows, columns=["game_id", "kickoff", "home_team", "away_team", "sport_key"])
    if not df.empty:
        ts = pd.to_datetime(df["kickoff"], errors="coerce", utc=True)
        df["kickoff"] = ts.dt.tz_localize(None)
    return df


def unwrap(payload: Any) -> list[dict]:
    """Return the events list from either a live array or a historical ``{data: …}``.

    The live ``/odds`` endpoint returns a bare array; the historical endpoint wraps
    it as ``{"timestamp": …, "data": [ … ]}``. This accepts either.
    """
    if isinstance(payload, Mapping):
        return list(payload.get("data") or [])
    return list(payload or [])


def events_of(payload: Any) -> list[dict]:
    """Event objects from any odds payload shape — including a lone per-event object.

    Superset of :func:`unwrap`: the bulk ``/odds`` endpoint returns an array and the
    historical endpoints wrap it as ``{"data": […]}`` (both handled by ``unwrap``),
    but the per-event ``/events/{id}/odds`` endpoint returns a **single event
    object** — which ``unwrap`` would read as empty (no ``data`` key). This flattens
    all three, so per-event props/derivatives normalize correctly.
    """
    if isinstance(payload, Mapping):
        if "data" in payload:
            return events_of(payload["data"])
        return [dict(payload)]  # a single per-event object
    return list(payload or [])


# ---------------------------------------------------------------------------
# Credit accounting
#
# The Odds API bills per call and puts the bill on the response:
# ``x-requests-last`` is what THIS call cost, ``x-requests-used`` /
# ``x-requests-remaining`` the running totals. A collector that prints
# "remaining" answers nothing a month later — the plan question is not "how
# many are left" but "which calls spend them", and only a banked series of
# per-call costs answers that. Hence a ledger: one row per credit-spending
# call, banked beside the data it bought.
# ---------------------------------------------------------------------------

USAGE_COLUMNS = [
    "at",
    "kind",
    "endpoint",
    "league",
    "markets",
    "regions",
    "cost",
    "used",
    "remaining",
]

# Endpoint path shape → ledger ``kind``. The shape is what recurs across runs;
# the raw path carries an event id, worth keeping but useless to group by.
_ENDPOINT_KINDS = {
    ("sports",): "sports",
    ("sports", "*", "odds"): "odds",
    ("sports", "*", "events"): "events",
    ("sports", "*", "events", "*", "odds"): "event_odds",
    ("historical", "sports", "*", "odds"): "historical_odds",
    ("historical", "sports", "*", "events"): "historical_events",
    ("historical", "sports", "*", "events", "*", "odds"): "historical_event_odds",
}
_PATH_WORDS = {"sports", "events", "odds", "historical"}


def classify_endpoint(endpoint: str) -> tuple[str, str]:
    """Return ``(kind, league)`` for an API path — the ledger's grouping keys.

    The league is in the *path* (``sports/baseball_mlb/odds``), never in the
    query, so reading it off the request params would bank a blank column. An
    unrecognised shape degrades to its masked path rather than raising:
    accounting never decides whether a fetch succeeded.
    """
    parts = [p for p in str(endpoint).strip("/").split("/") if p]
    league = ""
    for i, part in enumerate(parts):
        if part == "sports" and i + 1 < len(parts):
            key = parts[i + 1]
            league = LEAGUE_BY_SPORT_KEY.get(key, key)
            break
    shape = tuple(p if p in _PATH_WORDS else "*" for p in parts)
    return _ENDPOINT_KINDS.get(shape, "/".join(shape)), league


def _int_or_none(value: object) -> int | None:
    """Header value → int, or ``None`` when absent/unparseable (never raises)."""
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _empty_usage() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "at": pd.Series(dtype="datetime64[ns]"),
            "kind": pd.Series(dtype=str),
            "endpoint": pd.Series(dtype=str),
            "league": pd.Series(dtype=str),
            "markets": pd.Series(dtype=str),
            "regions": pd.Series(dtype=str),
            "cost": pd.Series(dtype="Int64"),
            "used": pd.Series(dtype="Int64"),
            "remaining": pd.Series(dtype="Int64"),
        }
    )


def usage_frame(rows: Iterable[Mapping[str, object]] | pd.DataFrame) -> pd.DataFrame:
    """Ledger rows (or an already-built frame) → the typed ledger shape.

    Counts are nullable ``Int64``: a response that omitted a header records a
    missing cost, which is not the same claim as a cost of zero. Idempotent, so
    the summary functions can normalize whatever they are handed.
    """
    if isinstance(rows, pd.DataFrame):
        frame = rows.copy()
    else:
        frame = pd.DataFrame([dict(row) for row in rows])
    if frame.empty:
        return _empty_usage()
    frame = frame.reindex(columns=USAGE_COLUMNS)
    # Pin the resolution: ``utcnow()`` is microsecond, the empty frame is
    # nanosecond, and ledgers from many runs get concatenated.
    frame["at"] = pd.to_datetime(frame["at"], errors="coerce").astype("datetime64[ns]")
    for col in ("kind", "endpoint", "league", "markets", "regions"):
        frame[col] = frame[col].fillna("").astype(str)
    for col in ("cost", "used", "remaining"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("Int64")
    return frame


def usage_summary(usage: Iterable[Mapping[str, object]] | pd.DataFrame) -> pd.DataFrame:
    """Per ``(kind, league)`` totals — calls, credits, credits per call.

    Dearest first, because right-sizing a plan is a question about the top few
    rows: one per-event pull across a full board outspends every game-line call
    in the run combined.
    """
    frame = usage_frame(usage)
    if frame.empty:
        return pd.DataFrame(
            {
                "kind": pd.Series(dtype=str),
                "league": pd.Series(dtype=str),
                "calls": pd.Series(dtype="int64"),
                "credits": pd.Series(dtype="int64"),
                "per_call": pd.Series(dtype=float),
            }
        )
    frame = frame.assign(cost=frame["cost"].fillna(0).astype("int64"))
    grouped = frame.groupby(["kind", "league"], as_index=False).agg(
        calls=("cost", "size"), credits=("cost", "sum")
    )
    grouped["per_call"] = grouped["credits"] / grouped["calls"]
    return grouped.sort_values(
        ["credits", "calls", "kind"], ascending=[False, False, True]
    ).reset_index(drop=True)


def write_usage(
    usage: Iterable[Mapping[str, object]], out_dir: Path, tag: str
) -> Path | None:
    """Bank a run's credit ledger beside the data it bought.

    Returns the written path, or ``None`` when the run made no calls (an
    off-season run banks nothing rather than an empty file). The ledger holds
    request *shapes* and response counts only — no key, no query string — so it
    is safe in an Actions artifact, which is not a private place
    (docs/DATA_PROVIDERS.md).
    """
    frame = usage_frame(usage)
    if frame.empty:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"odds_credits_{tag}.parquet"
    frame.to_parquet(dest, index=False)
    return dest


def describe_usage(usage: Iterable[Mapping[str, object]]) -> str:
    """One run's credit spend as run-log lines: total, then dearest kinds first."""
    frame = usage_frame(usage)
    if frame.empty:
        return "credits: no API calls this run"
    spent = int(frame["cost"].fillna(0).sum())
    left = frame["remaining"].dropna()
    last_left = _int_or_none(left.iloc[-1]) if not left.empty else None
    tail = "" if last_left is None else f"; {last_left} left this month"

    def _calls(n: int) -> str:
        return f"{n} call" if n == 1 else f"{n} calls"

    lines = [f"credits: {spent} spent over {_calls(len(frame))}{tail}"]
    summary = usage_summary(frame)
    for kind, league, calls, credits, per_call in zip(
        summary["kind"].astype(str),
        summary["league"].astype(str),
        summary["calls"].astype("int64"),
        summary["credits"].astype("int64"),
        summary["per_call"].astype(float),
        strict=True,
    ):
        lines.append(
            f"  {kind} [{league or '-'}]: {int(credits)} credits "
            f"over {_calls(int(calls))} ({float(per_call):.1f}/call)"
        )
    return "\n".join(lines)


def project_monthly(
    usage: Iterable[Mapping[str, object]] | pd.DataFrame,
    *,
    plan: int = 100_000,
    min_hours: float = 1.0,
) -> dict[str, float]:
    """Extrapolate a ledger window to a month's consumption against ``plan``.

    A window shorter than ``min_hours`` projects ``nan`` rather than a number:
    a handful of calls says nothing about a month, and a confident projection
    from one is worse than none. Feed this several days of banked ledgers
    before taking it to a plan change.
    """
    frame = usage_frame(usage)
    credits = float(frame["cost"].fillna(0).sum())
    out: dict[str, float] = {
        "calls": float(len(frame)),
        "credits": credits,
        "days": 0.0,
        "per_day": float("nan"),
        "monthly": float("nan"),
        "plan": float(plan),
        "plan_use_pct": float("nan"),
    }
    stamps = frame["at"].dropna()
    if stamps.empty:
        return out
    span_days = float((stamps.max() - stamps.min()).total_seconds()) / 86_400.0
    out["days"] = span_days
    if span_days * 24.0 < min_hours:
        return out
    per_day = credits / span_days
    out["per_day"] = per_day
    out["monthly"] = per_day * 30.0
    if plan:
        out["plan_use_pct"] = 100.0 * per_day * 30.0 / float(plan)
    return out


@dataclass
class TheOddsAPIClient:
    """Network client for The Odds API. Build with :meth:`from_env`.

    ``api_key`` comes from the ``THE_ODDS_API`` environment variable — never a
    literal — so the collector reads it from a GitHub Actions secret and the
    sandbox never sees it. Each response also carries the remaining-credit count in
    the ``x-requests-remaining`` header, surfaced by the fetchers that need it.

    Every credit-spending call also appends a row to :attr:`usage`, the credit
    ledger (:func:`usage_frame`), which collectors bank with :func:`write_usage`
    so plan sizing is a measurement rather than an estimate. A client is
    single-run: the ledger is its own calls, not a global tally.
    """

    api_key: str
    regions: str = "us"
    odds_format: str = "american"
    remaining: str | None = None  # credits left, from the last response header
    # One row per credit-spending call: endpoint, what it cost, what is left.
    # A printed "credits remaining" is a number in a log that ages out; a
    # banked series is what answers "are we on the right plan".
    usage: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> TheOddsAPIClient:
        api_key = os.environ.get("THE_ODDS_API", "")
        if not api_key:
            raise RuntimeError("THE_ODDS_API is not set (The Odds API key)")
        return cls(api_key=api_key)

    @staticmethod
    def sport_key(league: str) -> str:
        """Map a friendly league name to The Odds API sport key (pass-through if already one)."""
        return SPORT_KEYS.get(league.lower(), league)

    def _get(  # pragma: no cover - network
        self, endpoint: str, **params: object
    ) -> tuple[Any, dict[str, str]]:
        query = {"apiKey": self.api_key, **{k: v for k, v in params.items() if v is not None}}
        url = f"{_BASE}/{endpoint.lstrip('/')}?{urllib.parse.urlencode(query, doseq=True)}"
        with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
            headers = {
                "remaining": resp.headers.get("x-requests-remaining", ""),
                "used": resp.headers.get("x-requests-used", ""),
                # What THIS call cost. The plan question is not "how many are
                # left" but "which calls spend them", and only this header says.
                "last": resp.headers.get("x-requests-last", ""),
            }
            self._record(endpoint, params, headers)
            return json.loads(resp.read()), headers

    def _record(
        self, endpoint: str, params: Mapping[str, object], headers: Mapping[str, str]
    ) -> None:
        """Append one ledger row. Never raises — accounting must not break a fetch.

        Records the request's *shape* (kind, league, markets, regions) and the
        bill the response carried. Never the key: the signed query string is
        built in :meth:`_get` and does not reach here, and this frame is banked
        to an artifact, which is not a private place (docs/DATA_PROVIDERS.md).
        """
        try:
            kind, league = classify_endpoint(endpoint)
            markets = params.get("markets")
            regions = params.get("regions")
            self.usage.append(
                {
                    "at": pd.Timestamp.now("UTC").tz_localize(None),
                    "kind": kind,
                    "endpoint": str(endpoint).strip("/"),
                    "league": league,
                    "markets": "" if markets is None else str(markets),
                    "regions": "" if regions is None else str(regions),
                    "cost": _int_or_none(headers.get("last")),
                    "used": _int_or_none(headers.get("used")),
                    "remaining": _int_or_none(headers.get("remaining")),
                }
            )
        except Exception:  # noqa: BLE001 - accounting is never worth a failed run
            pass

    def usage_ledger(self) -> pd.DataFrame:
        """This client's credit ledger as a typed frame (see :func:`usage_frame`)."""
        return usage_frame(self.usage)

    def sports(self) -> list[dict]:  # pragma: no cover - network
        """Return the list of in-season sports (a cheap, credit-free call)."""
        data, _ = self._get("sports")
        return list(data or [])

    def odds_payload(  # pragma: no cover - network
        self, league: str, markets: str = "h2h,spreads,totals"
    ) -> Any:
        """Raw ``/odds`` payload for ``league`` — carries both lines and event metadata."""
        data, meta = self._get(
            f"sports/{self.sport_key(league)}/odds",
            regions=self.regions,
            markets=markets,
            oddsFormat=self.odds_format,
        )
        self.remaining = meta.get("remaining")
        return data

    def odds(  # pragma: no cover - network
        self, league: str, markets: str = "h2h,spreads,totals"
    ) -> pd.DataFrame:
        """Live game lines for ``league`` → a canonical ``Lines`` frame (snapshot, not closing)."""
        return normalize_odds_events(unwrap(self.odds_payload(league, markets)), is_closing=False)

    def historical_odds_payload(  # pragma: no cover - network
        self, league: str, date: str, markets: str = "h2h,spreads,totals"
    ) -> Any:
        """Raw historical ``/historical/.../odds`` payload at ``date`` (ISO 8601).

        A ``{"timestamp", "previous_timestamp", "next_timestamp", "data": [...]}``
        wrapper; the collector banks it verbatim so nothing the credits bought is
        lost. Updates :attr:`remaining` from the response header.
        """
        data, meta = self._get(
            f"historical/sports/{self.sport_key(league)}/odds",
            regions=self.regions,
            markets=markets,
            oddsFormat=self.odds_format,
            date=date,
        )
        self.remaining = meta.get("remaining")
        return data

    def historical_odds(
        self, league: str, date: str, markets: str = "h2h,spreads,totals"
    ) -> pd.DataFrame:  # pragma: no cover - network
        """Historical snapshot at ``date`` (ISO 8601) → canonical ``Lines`` (the CLV archive).

        The API returns the last snapshot at or before ``date``; pass a kickoff
        time to approximate the closing line, so these rows are marked closing.
        """
        return normalize_odds_events(
            unwrap(self.historical_odds_payload(league, date, markets)), is_closing=True
        )

    def event_ids(  # pragma: no cover - network
        self, league: str, *, days_ahead: float | None = None
    ) -> list[str]:
        """The current event ids for ``league`` (the cheap ``/events`` list).

        ``days_ahead`` keeps only events commencing within that window —
        the offseason board can carry a whole season (272 NFL events), and
        every id here becomes one PAID per-event call downstream.
        """
        data, _ = self._get(f"sports/{self.sport_key(league)}/events")
        events = data or []
        if days_ahead is not None:
            import pandas as pd

            horizon = pd.Timestamp.utcnow() + pd.Timedelta(days=days_ahead)
            events = [
                e for e in events
                if pd.to_datetime(e.get("commence_time"), errors="coerce",
                                  utc=True) <= horizon
            ]
        return [str(e["id"]) for e in events if e.get("id") is not None]

    def event_odds_payloads(  # pragma: no cover - network
        self, league: str, markets: str, *, days_ahead: float | None = 8.0
    ) -> list[tuple[str, Any]]:
        """Raw per-event ``/events/{id}/odds`` payloads for current events.

        One call per event (the only way The Odds API serves props + the additional
        markets — team totals, first-5-innings). Returns ``(event_id, raw payload)``
        pairs so a collector can bank them verbatim; nothing the credits bought is
        lost, even for markets without a normalizer yet. ``markets`` may combine prop
        and derivative keys in a single credit-efficient call. ``days_ahead``
        (default 8) clamps the paid calls to games actually near kickoff —
        props barely post further out anyway.
        """
        sport = self.sport_key(league)
        out: list[tuple[str, Any]] = []
        for event_id in self.event_ids(league, days_ahead=days_ahead):
            data, meta = self._get(
                f"sports/{sport}/events/{event_id}/odds",
                regions=self.regions,
                markets=markets,
                oddsFormat=self.odds_format,
            )
            self.remaining = meta.get("remaining")
            out.append((event_id, data))
        return out

    def player_props(  # pragma: no cover - network
        self, league: str, markets: str = DEFAULT_PROP_MARKETS
    ) -> pd.DataFrame:
        """Live player props for ``league`` → a canonical ``PropLines`` frame.

        Props are a per-event endpoint, so this pulls each event's board (via
        :meth:`event_odds_payloads`) and concatenates. Empty (no props posted) is a
        valid snapshot, not an error.
        """
        frames = [
            normalize_player_props(events_of(raw), is_closing=False)
            for _, raw in self.event_odds_payloads(league, markets)
        ]
        if not frames:
            return _empty_prop_lines()
        return PropLines.validate(pd.concat(frames, ignore_index=True))

    def team_totals(self, league: str) -> pd.DataFrame:  # pragma: no cover - network
        """Live team totals for ``league`` → a canonical ``Lines`` frame.

        Team totals are one of the "additional markets" The Odds API serves only
        per event, so this pulls each event's board and concatenates. Each row's
        market is ``team_total_home``/``team_total_away`` (resolved from the
        outcome's team description); sides are Over/Under. Empty (none posted)
        is a valid snapshot, not an error.
        """
        frames = [
            normalize_odds_events(events_of(raw), is_closing=False)
            for _, raw in self.event_odds_payloads(league, "team_totals")
        ]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return _empty_lines()
        return Lines.validate(pd.concat(frames, ignore_index=True))

    def historical_events_payload(  # pragma: no cover - network
        self, league: str, date: str
    ) -> Any:
        """Raw ``/historical/.../events`` list at ``date`` (ISO 8601) — cheap (1 credit).

        Props are per-event, so a historical props pull first needs the event ids that
        existed at the snapshot. The ``{data: [event…]}`` wrapper's events carry
        ``id`` + ``commence_time`` + teams — enough for :func:`extract_events`.
        """
        data, meta = self._get(f"historical/sports/{self.sport_key(league)}/events", date=date)
        self.remaining = meta.get("remaining")
        return data

    def historical_event_odds_payload(  # pragma: no cover - network
        self, league: str, event_id: str, date: str, markets: str = DEFAULT_PROP_MARKETS
    ) -> Any:
        """Raw per-event ``/historical/.../events/{id}/odds`` at ``date`` (ISO 8601).

        The historical counterpart of :meth:`event_odds_payloads` — one event, one
        snapshot. Costs 10 × markets × regions (historical multiplier), so the
        collector banks the ``{…, data: {event}}`` wrapper verbatim.
        """
        data, meta = self._get(
            f"historical/sports/{self.sport_key(league)}/events/{event_id}/odds",
            regions=self.regions,
            markets=markets,
            oddsFormat=self.odds_format,
            date=date,
        )
        self.remaining = meta.get("remaining")
        return data
