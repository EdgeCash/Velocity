"""NCAAB ingest — Bart Torvik ratings, live and as-of-date, normalized.

The NCAAB bootstrap (docs/EDGE_RESEARCH.md §3.1, docs/BUILD_NCAAB.md) starts
from Torvik's T-Rank data, which is uniquely suited to the repo's discipline:
alongside the live ratings, barttorvik.com serves a **timemachine** archive of
daily as-of-date ratings — the leak-free training input a walk-forward
backtest needs, for free.

Two payload shapes, one normalizer:

* ``{year}_team_results.csv`` — header-keyed CSV (the documented column set).
* ``{year}_team_results.json`` and
  ``timemachine/team_results/{yyyymmdd}_team_results.json.gz`` — the same
  rows as positional arrays in the CSV's column order (verified against the
  live endpoints, 2026-08).

Normalization keeps the modeling core: team, conference, adjusted offensive
and defensive efficiency (points per 100 possessions), adjusted tempo,
barthag (the power rating as P(win) vs an average team), rank, and
wins-above-bubble. Everything else on the wire is dropped — the model layer
decomposes score = pace × efficiency and needs exactly these.

The author asks bulk scrapers to make contact first (his data page); the
collector fetches once per day per date, which is the polite cadence.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import urllib.request
from collections.abc import Collection, Iterable, Sequence
from typing import Any

import pandas as pd

_BASE = "https://barttorvik.com"
_FETCH_TIMEOUT = 30

# The team_results header, as served by the CSV endpoint (fetched 2026-08).
# The JSON endpoints serve the same rows positionally in this order, so this
# tuple is the single source of truth for both shapes.
TEAM_RESULTS_COLUMNS: tuple[str, ...] = (
    "rank", "team", "conf", "record", "adjoe", "oe Rank", "adjde", "de Rank",
    "barthag", "rank2", "proj. W", "Proj. L", "Pro Con W", "Pro Con L",
    "Con Rec.", "sos", "ncsos", "consos", "Proj. SOS", "Proj. Noncon SOS",
    "Proj. Con SOS", "elite SOS", "elite noncon SOS", "Opp OE", "Opp DE",
    "Opp Proj. OE", "Opp Proj DE", "Con Adj OE", "Con Adj DE", "Qual O",
    "Qual D", "Qual Barthag", "Qual Games", "FUN", "ConPF", "ConPA",
    "ConPoss", "ConOE", "ConDE", "ConSOSRemain", "Conf Win%", "WAB",
    "WAB Rk", "Fun Rk", "adjt",
)

# Canonical output: wire name → our name. The modeling core only.
_KEEP: tuple[tuple[str, str], ...] = (
    ("team", "team"),
    ("conf", "conf"),
    ("rank", "rank"),
    ("adjoe", "adj_o"),
    ("adjde", "adj_d"),
    ("adjt", "adj_t"),
    ("barthag", "barthag"),
    ("WAB", "wab"),
)
_NUMERIC = ("rank", "adj_o", "adj_d", "adj_t", "barthag", "wab")


def team_results_url(year: int) -> str:
    """The live season-to-date ratings endpoint (JSON) for a season year."""
    return f"{_BASE}/{int(year)}_team_results.json"


def timemachine_url(date: str | pd.Timestamp) -> str:
    """The as-of-date archive endpoint for one day (gzipped JSON).

    ``date`` is any timestamp-like; the archive keys days as ``YYYYMMDD``.
    These are the ratings exactly as they stood that morning — the leak-free
    input for a walk-forward backtest.
    """
    stamp = pd.Timestamp(date).strftime("%Y%m%d")
    return f"{_BASE}/timemachine/team_results/{stamp}_team_results.json.gz"


def normalize_team_results(
    payload: Iterable[Sequence[Any]] | pd.DataFrame,
    *,
    season: int | None = None,
    as_of: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Torvik team results (positional JSON rows or a header-keyed frame) → ratings.

    Output columns: ``team/conf/rank/adj_o/adj_d/adj_t/barthag/wab`` plus
    ``season`` and ``as_of`` when supplied. Rows shorter than the header are
    padded with NA (early-season archives can trail off); longer rows keep
    their leading positions. Tolerant like every ingest normalizer — a
    malformed row contributes NA fields, never an exception.
    """
    if isinstance(payload, pd.DataFrame):
        frame = payload.copy()
    else:
        width = len(TEAM_RESULTS_COLUMNS)
        rows = []
        for raw in payload or []:
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                continue
            row = list(raw)[:width]
            row += [None] * (width - len(row))
            rows.append(row)
        frame = pd.DataFrame(rows, columns=list(TEAM_RESULTS_COLUMNS))

    out = pd.DataFrame()
    for wire, ours in _KEEP:
        out[ours] = frame[wire] if wire in frame.columns else pd.Series(dtype=object)
    out["team"] = out["team"].astype(str)
    for col in _NUMERIC:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["adj_o", "adj_d"]).reset_index(drop=True)
    if season is not None:
        out["season"] = int(season)
    if as_of is not None:
        out["as_of"] = pd.Timestamp(as_of)
    return out


def expected_matchup(
    ratings: pd.DataFrame, home: str, away: str, *, hca_points: float = 3.0
) -> dict[str, float] | None:
    """A first-cut expected score from the ratings alone (the model's prior).

    Standard possession decomposition: expected pace is the mean of the two
    adjusted tempos; each side's points per 100 possessions is its adjusted
    offense scaled by the opponent's adjusted defense against the national
    average implied by the frame; home court is a flat ``hca_points`` split.
    This is the *baseline the market already prices* — the model layer's job
    is what Torvik omits (injuries, venues, derivatives). Returns ``None``
    when either team is missing, never a guess.
    """
    by_team: dict[str, dict[str, float]] = {
        str(row["team"]): {
            "adj_o": float(row["adj_o"]), "adj_d": float(row["adj_d"]),
            "adj_t": float(row["adj_t"]),
        }
        for row in ratings.to_dict("records")
    }
    if home not in by_team or away not in by_team:
        return None
    h, a = by_team[home], by_team[away]
    avg_o = float(ratings["adj_o"].mean())
    pace = (h["adj_t"] + a["adj_t"]) / 2.0
    home_pp100 = h["adj_o"] * a["adj_d"] / avg_o
    away_pp100 = a["adj_o"] * h["adj_d"] / avg_o
    home_pts = home_pp100 * pace / 100.0 + hca_points / 2.0
    away_pts = away_pp100 * pace / 100.0 - hca_points / 2.0
    return {
        "pace": pace,
        "home_points": home_pts,
        "away_points": away_pts,
        "total": home_pts + away_pts,
        "margin": home_pts - away_pts,
    }


# ---------------------------------------------------------------------------
# hoopR (sportsdataverse) — schedules and team boxes, the model's game data.
# ESPN's own edge 403s datacenter IPs; the raw-CDN parquet mirror is the
# proven transport (the WNBA vertical's wehoop pattern, same column shapes).
# ---------------------------------------------------------------------------

HOOPR_SCHEDULE_URL = (
    "https://raw.githubusercontent.com/sportsdataverse/hoopr-mbb-data/main/"
    "mbb/schedules/parquet/mbb_schedule_{season}.parquet"
)
HOOPR_TEAM_BOX_URL = (
    "https://raw.githubusercontent.com/sportsdataverse/hoopr-mbb-data/main/"
    "mbb/team_box/parquet/team_box_{season}.parquet"
)
_ESPN_SEASON_TYPES = {1: "PRE", 2: "REG", 3: "POST"}
# Possession components, the exact slim shape the WNBA box dataset commits.
TEAM_BOX_KEEP = (
    "game_id", "team_home_away", "field_goals_attempted",
    "offensive_rebounds", "total_turnovers", "free_throws_attempted",
)


def ncaab_week(kickoff: pd.Timestamp, season: int) -> int:
    """A date-monotone week bucket for a season that crosses the year boundary.

    The daily-league convention (``inseason._season_week``) buckets day-of-
    year, which inverts across New Year — a college January would sort
    *before* its own November. NCAAB anchors at Nov 1 of the season's first
    calendar year (season 2025 = 2024-25), 15-day buckets, clamped to the
    schema's 0–25.
    """
    start = pd.Timestamp(year=int(season) - 1, month=11, day=1)
    days = (pd.Timestamp(kickoff) - start).days
    return min(max(int(days // 15), 0), 25)


def normalize_hoopr_schedule(raw: pd.DataFrame, season: int) -> pd.DataFrame:
    """A hoopR season schedule parquet → the canonical completed ``Games`` frame.

    Teams key by ESPN ``location`` (the school-ish name — the same space the
    Torvik prior joins on, imperfectly; unmatched priors simply don't apply).
    Rows missing an id, either team, a parseable kickoff, or final scores are
    skipped, never guessed — the frame is *completed games*, the ratings
    fit's input.
    """
    from velocity.store.schema import Games

    rows: list[dict[str, object]] = []
    for g in raw.to_dict("records"):
        game_id = g.get("game_id") or g.get("id")
        home = g.get("home_location")
        away = g.get("away_location")
        if game_id is None or not home or not away:
            continue
        if not bool(g.get("status_type_completed", False)):
            continue
        raw_ts = g.get("game_date_time") or g.get("game_date")
        if raw_ts is None:
            continue
        kickoff = pd.to_datetime(str(raw_ts), errors="coerce", utc=True)
        if pd.isna(kickoff):
            continue
        try:
            home_score = float(g.get("home_score"))  # type: ignore[arg-type]
            away_score = float(g.get("away_score"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        kickoff = kickoff.tz_localize(None)
        season_type = _ESPN_SEASON_TYPES.get(int(g.get("season_type") or 2), "REG")
        if season_type == "PRE":
            continue  # exhibitions are projection noise, same as MLB spring
        rows.append({
            "game_id": str(game_id),
            "league": "ncaab",
            "season": int(season),
            "week": ncaab_week(kickoff, season),
            "season_type": season_type,
            "kickoff": kickoff,
            "home_team": str(home),
            "away_team": str(away),
            "neutral_site": bool(g.get("neutral_site", False)),
            "roof": None,
            "surface": None,
            "home_score": home_score,
            "away_score": away_score,
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.drop_duplicates(subset="game_id", keep="last").reset_index(drop=True)
    return Games.validate(frame)


def normalize_hoopr_team_box(raw: pd.DataFrame, season: int) -> pd.DataFrame:
    """A hoopR team-box parquet → the slim possession-components frame.

    Identical shape to the committed WNBA box (``build_wnba_box.slim_team_box``)
    so ``wnba_pace_frame`` consumes it unchanged.
    """
    out = raw[list(TEAM_BOX_KEEP)].copy()
    out["game_id"] = out["game_id"].astype(str)
    out["team_home_away"] = out["team_home_away"].astype(str)
    for col in TEAM_BOX_KEEP[2:]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["season"] = int(season)
    return out


# ---------------------------------------------------------------------------
# The Torvik prior as pseudo-games — the ridge fit's answer to weak schedule
# connectivity. 360+ teams playing conference-clustered ~30-game schedules
# leave the scores ridge badly compressed (walk-forward calibration slope
# act≈2.6·pred at λ=50): cross-cluster strength differences shrink toward
# zero. Instead of hand-blending ratings, last season's final Torvik ratings
# enter as K synthetic week-0 games per team against a shared ``__PRIOR__``
# anchor — the existing ridge + recency machinery then absorbs the prior and
# decays it naturally as real games arrive.
# ---------------------------------------------------------------------------

PRIOR_ANCHOR = "__PRIOR__"

# Torvik name → hoopR/ESPN ``location`` candidates (most-likely first).
# Validated against seasons 2019–2026: with these plus the St.→State
# expansion, every Torvik rating row maps to a hoopR team name.
TORVIK_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "American": ("American University",),
    "Arkansas Pine Bluff": ("Arkansas-Pine Bluff",),
    "Bethune Cookman": ("Bethune-Cookman",),
    "Cal Baptist": ("California Baptist",),
    "Connecticut": ("UConn",),
    "FIU": ("Florida International",),
    "Gardner Webb": ("Gardner-Webb",),
    "Grambling St.": ("Grambling",),
    "Hawaii": ("Hawai'i",),
    "IU Indy": ("IU Indianapolis", "IUPUI"),
    "IUPUI": ("IUPUI", "IU Indianapolis"),
    "Illinois Chicago": ("UIC",),
    "LIU": ("Long Island University",),
    "Louisiana Monroe": ("UL Monroe",),
    "Loyola MD": ("Loyola Maryland",),
    "McNeese St.": ("McNeese",),
    "Miami FL": ("Miami",),
    "Miami OH": ("Miami (OH)",),
    "Mississippi": ("Ole Miss",),
    "N.C. State": ("NC State",),
    "Nebraska Omaha": ("Omaha",),
    "Nicholls St.": ("Nicholls",),
    "Penn": ("Pennsylvania",),
    "Queens": ("Queens University",),
    "Sam Houston St.": ("Sam Houston",),
    "San Jose St.": ("San José State",),
    "Seattle": ("Seattle U",),
    "Southeastern Louisiana": ("SE Louisiana",),
    "St. Francis NY": ("St. Francis Brooklyn",),
    "St. Thomas": ("St. Thomas-Minnesota", "St. Thomas - Minnesota"),
    "Tennessee Martin": ("UT Martin",),
    "Texas A&M Corpus Chris": ("Texas A&M-Corpus Christi",),
    "UMKC": ("Kansas City",),
    "USC Upstate": ("South Carolina Upstate",),
}


def torvik_team_candidates(name: str) -> tuple[str, ...]:
    """hoopR-name candidates for a Torvik team name, most-likely first.

    Torvik abbreviates "State" as "St." (except leading "St.", which is
    Saint); everything that rule can't fix is in the hand-checked alias
    table. Callers pick the first candidate present in their team universe.
    """
    if name in TORVIK_TEAM_ALIASES:
        return TORVIK_TEAM_ALIASES[name] + (name,)
    expanded = re.sub(r"(?<!^)\bSt\.(?=( |$))", "State", name)
    return (name, expanded) if expanded != name else (name,)


def torvik_pseudo_games(
    torvik: pd.DataFrame,
    teams: Collection[str],
    *,
    cutoff: pd.Timestamp,
    k: int = 6,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Last season's Torvik ratings → K synthetic week-0 games per team.

    Returns ``(games, pace)`` frames shaped for
    :func:`velocity.backtest.lab.fit_pace_efficiency`: each rating row for
    Torvik season ``s`` becomes ``k`` copies of a neutral-site week-0 game of
    season ``s+1`` — team vs :data:`PRIOR_ANCHOR` with ``poss = adj_t`` and
    scores ``adj_o·adj_t/100`` / ``adj_d·adj_t/100``, so the fit's per-100
    conversion recovers exactly ``adj_o``/``adj_d``. The anchor plays every
    team, so its own fitted rating settles at the D1 average and re-centers
    Torvik's scale onto the fit's base.

    Leak gate: a rating row enters only when its season was *finished* at the
    caller's knowledge point — ``cutoff`` (the latest real kickoff in the
    training slice) must reach April 10 of the rating season's closing
    calendar year. Neutral-site keeps the pseudo-games out of the home-edge
    column; ``k`` is the prior's weight in games. Teams outside ``teams``
    (name drift, non-D1) contribute nothing, never a guess.
    """
    game_rows: list[dict[str, object]] = []
    pace_rows: list[dict[str, object]] = []
    cutoff = pd.Timestamp(cutoff)
    for r in torvik.to_dict("records"):
        rating_season = int(r["season"])
        if pd.Timestamp(year=rating_season, month=4, day=10) > cutoff:
            continue
        team = next(
            (c for c in torvik_team_candidates(str(r["team"])) if c in teams), None
        )
        if team is None:
            continue
        poss = float(r["adj_t"])
        if not poss > 0:
            continue
        season = rating_season + 1
        for i in range(int(k)):
            gid = f"prior-{season}-{r['team']}-{i}"
            game_rows.append({
                "game_id": gid,
                "season": season,
                "week": 0,
                "home_team": team,
                "away_team": PRIOR_ANCHOR,
                "home_score": float(r["adj_o"]) * poss / 100.0,
                "away_score": float(r["adj_d"]) * poss / 100.0,
                "neutral_site": True,
            })
            pace_rows.append({"game_id": gid, "poss": poss})
    return pd.DataFrame(game_rows), pd.DataFrame(pace_rows)


class TorvikClient:
    """Network client for the Torvik endpoints (the collector's transport)."""

    def _get(self, url: str) -> bytes:  # pragma: no cover - network
        req = urllib.request.Request(url, headers={"User-Agent": "velocity-collector"})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
            return resp.read()

    def team_results(self, year: int) -> pd.DataFrame:  # pragma: no cover - network
        """Live season-to-date ratings for ``year`` → the canonical frame."""
        payload = json.loads(self._get(team_results_url(year)))
        return normalize_team_results(payload, season=year)

    def team_results_asof(
        self, date: str | pd.Timestamp, *, season: int | None = None
    ) -> pd.DataFrame:  # pragma: no cover - network
        """Archived as-of-date ratings for ``date`` → the canonical frame."""
        raw = self._get(timemachine_url(date))
        payload = json.loads(gzip.GzipFile(fileobj=io.BytesIO(raw)).read())
        return normalize_team_results(payload, season=season, as_of=date)


def load_hoopr_schedule(season: int) -> pd.DataFrame:  # pragma: no cover - network
    """Fetch + normalize one season's hoopR schedule (network)."""
    return normalize_hoopr_schedule(pd.read_parquet(HOOPR_SCHEDULE_URL.format(season=season)),
                                    season)


def load_hoopr_team_box(season: int) -> pd.DataFrame:  # pragma: no cover - network
    """Fetch + slim one season's hoopR team boxes (network)."""
    return normalize_hoopr_team_box(pd.read_parquet(HOOPR_TEAM_BOX_URL.format(season=season)),
                                    season)
