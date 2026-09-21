"""``props.csv`` — the player-prop board, with the distribution behind each line.

The staked prop rows carry the price, the edge and the stake. What they have
never carried is the **shape** the sim drew them from: a prop is a bet on a
distribution, and a board that shows only its mean hides the difference
between a 70-yard floor and a 70-yard ceiling.

``prop_distribution_frame`` is that shape, taken from the same
``FootballPropSim`` samples the slate priced off — mean, median and the 75th,
90th and 99th percentiles — so the percentile columns are the simulation's
own quantiles, not a fitted curve through its mean. The live runner banks it
as ``prop_dist_{league}_{stamp}.parquet``; when that frame is absent the
percentile cells are empty and the rest of the row still exports.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from velocity.export.meta import (
    EXPORT_DIR,
    ExportMeta,
    numeric_column,
    round_columns,
    write_csv,
)
from velocity.util.names import fold_name
from velocity.wagering.plays import matchup_text

PROPS_COLUMNS: tuple[str, ...] = (
    "player",
    "team",
    "matchup",
    "kickoff",
    "market",
    "line",
    "projection",
    "median",
    "75th_percentile",
    "90th_percentile",
    "99th_percentile",
    "hit_probability",
    "fair_price",
    "market_price",
    "edge",
    "recommended_stake",
    "generated_at",
    "season",
    "week",
)

# The columns the banked distribution frame carries. ``player`` is the
# provider-facing name so the join below is a name fold rather than a guess
# at two id spaces agreeing.
PROP_DIST_COLUMNS: tuple[str, ...] = (
    "game_id",
    "player_key",
    "player",
    "team",
    "position",
    "market",
    "projection",
    "median",
    "p75",
    "p90",
    "p99",
    "n_sims",
)

# What the export calls each quantile, and where it comes from.
_PERCENTILES: tuple[tuple[str, float], ...] = (
    ("median", 50.0),
    ("p75", 75.0),
    ("p90", 90.0),
    ("p99", 99.0),
)

_DIST_TO_EXPORT = {
    "median": "median",
    "p75": "75th_percentile",
    "p90": "90th_percentile",
    "p99": "99th_percentile",
}


def _samples_for(sim: Any, player_key: str, market: str) -> np.ndarray | None:
    """The per-sim draws for one (player, market), or ``None``.

    Duck-typed on the sim rather than imported by type: the football, college
    and MLB prop sims are different classes that all answer ``has`` and expose
    their samples, and the export has no business knowing which league it is
    reading.
    """
    try:
        if not sim.has(player_key, market):
            return None
    except Exception:  # noqa: BLE001 - a sim that cannot answer simply abstains
        return None
    getter = getattr(sim, "player_samples", None)
    if getter is None:
        return None
    try:
        samples = np.asarray(getter(player_key, market), dtype=float)
    except Exception:  # noqa: BLE001 - same
        return None
    return samples if samples.size else None


def prop_distribution_frame(
    props_by_game: Mapping[str, Any],
    roster: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Per (game, player, market): the sim's mean, median and upper quantiles.

    ``roster`` is :func:`velocity.report.matchup.roster_from_projections` —
    ``player_key → name, position, team``. Without it the frame still builds,
    keyed by the sim's own player key, and the name and team cells are empty.
    """
    identity: dict[str, dict[str, str]] = {}
    if roster is not None and not roster.empty and "player_key" in roster.columns:
        for row in roster.to_dict("records"):
            identity[str(row["player_key"])] = {
                "player": str(row.get("player_name") or ""),
                "team": str(row.get("team") or ""),
                "position": str(row.get("position") or ""),
            }

    rows: list[dict[str, object]] = []
    for game_id, sim in props_by_game.items():
        keys = getattr(sim, "samples", None)
        if not keys:
            continue
        for player_key, market in sorted(keys):
            samples = _samples_for(sim, str(player_key), str(market))
            if samples is None:
                continue
            who = identity.get(str(player_key), {})
            quantiles = np.percentile(samples, [q for _, q in _PERCENTILES])
            record: dict[str, object] = {
                "game_id": str(game_id),
                "player_key": str(player_key),
                "player": who.get("player", ""),
                "team": who.get("team", ""),
                "position": who.get("position", ""),
                "market": str(market),
                "projection": float(np.mean(samples)),
                "n_sims": int(samples.size),
            }
            for (name, _), value in zip(_PERCENTILES, quantiles, strict=True):
                record[name] = float(value)
            rows.append(record)
    frame = pd.DataFrame(rows, columns=list(PROP_DIST_COLUMNS))
    return round_columns(frame, ("projection", "median", "p75", "p90", "p99"), 3)


def _fair_price(p_fair: object) -> float | None:
    """The de-vigged probability as an American price, or ``None``.

    The prop board quotes prices, so the fair number is quoted the same way;
    comparing a decimal to an American price is how a reader mis-reads a
    board twice and believes it once.
    """
    value = pd.to_numeric(pd.Series([p_fair]), errors="coerce").iloc[0]
    if pd.isna(value) or not 0.0 < float(value) < 1.0:
        return None
    from velocity.wagering.odds import prob_to_american

    return float(prob_to_american(float(value)))


def game_index(games: pd.DataFrame | None) -> dict[str, tuple[str | None, str | None]]:
    """``game_id`` → (matchup, kickoff), for boards that name a player not a game.

    Built here rather than joined so a prop whose ``game_id`` is absent from
    the board keeps its row with empty cells, instead of being dropped by a
    merge for a reason that has nothing to do with the bet.
    """
    out: dict[str, tuple[str | None, str | None]] = {}
    if games is None or games.empty or "game_id" not in games.columns:
        return out
    has_kickoff = "kickoff" in games.columns
    for row in games.drop_duplicates(subset=["game_id"]).to_dict("records"):
        kickoff: str | None = None
        if has_kickoff:
            when = pd.to_datetime(str(row.get("kickoff")), errors="coerce", utc=True)
            if not pd.isna(when):
                kickoff = pd.Timestamp(when).strftime("%Y-%m-%dT%H:%M:%SZ")
        out[str(row.get("game_id"))] = (
            matchup_text(home_team=str(row.get("home_team") or ""),
                         away_team=str(row.get("away_team") or "")),
            kickoff,
        )
    return out


def build_props(
    props: pd.DataFrame | None,
    distributions: pd.DataFrame | None = None,
    games: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """The props table, before metadata is stamped on it.

    ``props`` is a ``prop_slate_to_frame`` output (the banked
    ``slate_{league}_props_{stamp}.parquet``); ``distributions`` is
    :func:`prop_distribution_frame`. Rows are the staked/paper prop bets —
    the board the operator is being handed, not every line the book posted.
    """
    base = [c for c in PROPS_COLUMNS if c not in ("generated_at", "season", "week")]
    if props is None or props.empty:
        return pd.DataFrame(columns=base)

    out = pd.DataFrame(
        {
            "player": props["player"].astype(str) if "player" in props else "",
            "market": props["market"].astype(str) if "market" in props else "",
            "line": numeric_column(props, "point"),
            "hit_probability": numeric_column(props, "p_model"),
            "market_price": numeric_column(props, "price"),
            "edge": numeric_column(props, "edge"),
            "recommended_stake": numeric_column(props, "stake"),
        }
    )
    out["fair_price"] = [_fair_price(v) for v in props.get("p_fair", pd.Series(dtype=float))]
    out["team"] = ""
    out["matchup"] = None
    out["kickoff"] = None
    for name in ("projection", *(_DIST_TO_EXPORT[k] for k in _DIST_TO_EXPORT)):
        out[name] = np.nan

    if distributions is not None and not distributions.empty:
        dist = distributions.copy()
        dist["_fold"] = dist["player"].astype(str).map(fold_name)
        dist["_market"] = dist["market"].astype(str)
        # One row per (player, market): a player projected in two games on the
        # same export — a rarity, but a doubleheader is real — keeps the first
        # rather than multiplying the board.
        dist = dist.drop_duplicates(subset=["_fold", "_market"])
        keyed = dist.set_index(["_fold", "_market"])
        folds = out["player"].map(fold_name)
        pairs = list(zip(folds, out["market"], strict=True))

        def pull(column: str) -> list[float | None]:
            if column not in keyed.columns:
                return [None] * len(out)
            series = keyed[column]
            return [
                float(series.loc[key]) if key in series.index and pd.notna(series.loc[key])
                else None
                for key in pairs
            ]

        team = keyed["team"] if "team" in keyed.columns else None
        if team is not None:
            out["team"] = [
                str(team.loc[key]) if key in team.index else "" for key in pairs
            ]
        by_game = game_index(games)
        gids = keyed["game_id"] if "game_id" in keyed.columns else None
        if gids is not None and by_game:
            found = [
                by_game.get(str(gids.loc[key])) if key in gids.index else None
                for key in pairs
            ]
            out["matchup"] = [None if f is None else f[0] for f in found]
            out["kickoff"] = [None if f is None else f[1] for f in found]
        out["projection"] = pull("projection")
        for source, target in _DIST_TO_EXPORT.items():
            out[target] = pull(source)

    out = round_columns(out, ("line", "projection", *_DIST_TO_EXPORT.values()), 3)
    out = round_columns(out, ("hit_probability", "edge"), 4)
    out = round_columns(out, ("fair_price", "market_price"), 0)
    out = round_columns(out, ("recommended_stake",), 2)
    return out.reindex(columns=base)


def export_props(
    meta: ExportMeta,
    props: pd.DataFrame | None,
    distributions: pd.DataFrame | None = None,
    games: pd.DataFrame | None = None,
    *,
    out_dir: str | Path = EXPORT_DIR,
) -> Path:
    """Build and write ``props.csv``. Returns the path."""
    return write_csv(
        build_props(props, distributions, games),
        Path(out_dir) / "props.csv", PROPS_COLUMNS, meta,
    )
