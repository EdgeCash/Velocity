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
import urllib.request
from collections.abc import Iterable, Sequence
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
