"""``dfs.csv`` and ``dfs_optimizer.csv`` — the DraftKings pool, priced by contest.

The DFS pool the pipeline banks (``dfs_pool_{league}_{stamp}.parquet``) is a
mean and a salary. A mean is the right number for a cash game and the wrong
number for a tournament: cash pays for the median, single-entry for a good
day, and a GPP is won in the tail. The contest columns here are the *same
player's distribution* read at four points —

| Contest      | Column         | Quantile |
|--------------|----------------|----------|
| Cash         | ``cash``       | 50th     |
| Single entry | ``single_entry`` | 75th   |
| GPP          | ``gpp``        | 90th     |
| Ceiling      | ``ceiling``    | 99th     |

— taken from the per-sim DK-point arrays the NFL sim already builds for the
GPP tail scorer (``scripts/build_dfs_lineup.nfl_sim_projections``), banked as
``dfs_dist_{league}_{stamp}.parquet``. Without that frame the quantile
columns are empty and the pool still exports with its salary, projection and
value: a board with no simulated tail should say so rather than reprint the
mean four times under four different headings.

``ownership`` has no source in this repository. DraftKings does not publish
pre-lock ownership and nothing here models it, so the column exports empty
unless a projection is passed in — and ``leverage_score``, which is only
meaningful against ownership, goes empty with it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

from velocity.export.meta import (
    EXPORT_DIR,
    ExportMeta,
    numeric_column,
    round_columns,
    write_csv,
)

DFS_COLUMNS: tuple[str, ...] = (
    "player",
    "team",
    "position",
    "salary",
    "projection",
    "median",
    "ceiling",
    "ownership",
    "value_score",
    "leverage_score",
    "stack_rating",
    "generated_at",
    "season",
    "week",
)

DFS_OPTIMIZER_COLUMNS: tuple[str, ...] = (
    "player",
    "team",
    "position",
    "salary",
    "projection",
    "cash",
    "single_entry",
    "gpp",
    "ceiling",
    "ownership",
    "value_score",
    "leverage_score",
    "stack_rating",
    "generated_at",
    "season",
    "week",
)

DFS_DIST_COLUMNS: tuple[str, ...] = (
    "player", "projection", "median", "p75", "p90", "p99", "n_sims",
)

# The contest map, in one place, so the two exports and the docs cannot
# disagree about which quantile a GPP column is.
CONTEST_QUANTILES: tuple[tuple[str, str], ...] = (
    ("cash", "median"),
    ("single_entry", "p75"),
    ("gpp", "p90"),
    ("ceiling", "p99"),
)

_QUANTILES: tuple[tuple[str, float], ...] = (
    ("median", 50.0), ("p75", 75.0), ("p90", 90.0), ("p99", 99.0),
)

# How many same-team partners a stack rating counts. Three is the shape the
# GPP builder asks for — a QB and two pass-catchers (velocity/dfs/gpp.py) —
# so the rating measures the thing the portfolio actually tries to build.
_STACK_PARTNERS = 3


def dfs_distribution_frame(samples: Mapping[str, object]) -> pd.DataFrame:
    """Per-player DK-point quantiles from the sim's own per-sim arrays."""
    rows: list[dict[str, object]] = []
    for player, raw in samples.items():
        array = np.asarray(raw, dtype=float)
        array = array[np.isfinite(array)]
        if array.size == 0:
            continue
        values = np.percentile(array, [q for _, q in _QUANTILES])
        row: dict[str, object] = {
            "player": str(player),
            "projection": float(np.mean(array)),
            "n_sims": int(array.size),
        }
        for (name, _), value in zip(_QUANTILES, values, strict=True):
            row[name] = float(value)
        rows.append(row)
    frame = pd.DataFrame(rows, columns=list(DFS_DIST_COLUMNS))
    return round_columns(frame, ("projection", "median", "p75", "p90", "p99"), 2)


def stack_rating(pool: pd.DataFrame, partners: int = _STACK_PARTNERS) -> pd.Series:
    """How good a stacking environment each player sits in, on 0-10.

    The strength of a player's *teammates*, not of the player: the summed
    projection of the best ``partners`` other players on their team, min-max
    scaled across the slate. A high rating says a lineup that builds around
    this team has strong partners available — which is what a stack is.

    Deliberately not a correlation estimate. The repo measures correlation
    properly in ``velocity/eval/correlation.py`` against realized games; a
    second, weaker estimate computed off a salary board would compete with it
    for the same name.
    """
    if pool.empty or "team" not in pool.columns:
        return pd.Series([np.nan] * len(pool), index=pool.index, dtype=float)
    source = "points" if "points" in pool.columns else "projection"
    points = numeric_column(pool, source)
    frame = pd.DataFrame({"team": pool["team"].astype(str), "points": points})
    raw: list[float] = []
    for idx, row in zip(frame.index, frame.to_dict("records"), strict=True):
        mates = frame[(frame["team"] == row["team"]) & (frame.index != idx)]
        best = mates["points"].dropna().nlargest(partners)
        raw.append(float(best.sum()) if not best.empty else np.nan)
    series = pd.Series(raw, index=pool.index, dtype=float)
    low, high = series.min(), series.max()
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        # Every player in the same environment — a one-game slate, or a board
        # with no projections. A flat 5 would read as a measurement; nothing
        # is the truthful answer.
        return pd.Series([np.nan] * len(pool), index=pool.index, dtype=float)
    return ((series - low) / (high - low) * 10.0).round(1)


def leverage_score(projection: pd.Series, ownership: pd.Series) -> pd.Series:
    """Projection strength against ownership, on 0-10.

    Both sides are percentile-ranked within the slate, so the score is
    "well projected relative to the field, and under-owned relative to the
    field" rather than a ratio that explodes at 0.1% ownership. 10 is the
    best projection at the lowest ownership; 5 is a player the field has
    priced correctly.
    """
    proj = pd.to_numeric(projection, errors="coerce")
    own = pd.to_numeric(ownership, errors="coerce")
    if proj.notna().sum() < 2 or own.notna().sum() < 2:
        return pd.Series([np.nan] * len(proj), index=proj.index, dtype=float)
    proj_rank = proj.rank(pct=True)
    own_rank = own.rank(pct=True)
    return (((proj_rank - own_rank) + 1.0) / 2.0 * 10.0).round(1)


def _with_distribution(pool: pd.DataFrame, distribution: pd.DataFrame | None) -> pd.DataFrame:
    """``pool`` with the quantile columns joined on player name."""
    out = pool.copy()
    for name, _ in _QUANTILES:
        out[name] = np.nan
    if distribution is None or distribution.empty or "player" not in distribution.columns:
        return out
    dist = distribution.drop_duplicates(subset=["player"]).set_index(
        distribution.drop_duplicates(subset=["player"])["player"].astype(str)
    )
    names = out["player"].astype(str)
    for name, _ in _QUANTILES:
        if name in dist.columns:
            column = pd.to_numeric(dist[name], errors="coerce")
            out[name] = [
                float(column.loc[n]) if n in column.index and pd.notna(column.loc[n])
                else np.nan
                for n in names
            ]
    return out


def _base_pool(
    pool: pd.DataFrame | None,
    distribution: pd.DataFrame | None,
    ownership: Mapping[str, float] | pd.Series | None,
) -> pd.DataFrame:
    """The shared spine of both DFS exports: identity, salary, value, ratings."""
    if pool is None or pool.empty:
        return pd.DataFrame()
    out = pd.DataFrame(
        {
            "player": pool.get("player_name", pool.get("player", "")),
            "team": pool.get("team", ""),
            "position": pool.get("position", ""),
            "salary": numeric_column(pool, "salary"),
            "projection": numeric_column(
                pool, "points" if "points" in pool.columns else "projection"
            ),
        }
    )
    out["player"] = out["player"].astype(str)
    out["team"] = out["team"].astype(str)
    out["position"] = out["position"].astype(str)

    # DK's own convention: projected points per $1,000 of salary. A salary of
    # zero (the salary-free Tiers boards) has no value per dollar and says so
    # with a null rather than an infinity — the same rule pool_frame applies.
    if "value" in pool.columns:
        out["value_score"] = pd.to_numeric(pool["value"], errors="coerce")
    else:
        out["value_score"] = (out["projection"] / (out["salary"] / 1000.0)).where(
            out["salary"] > 0
        )

    out = _with_distribution(out, distribution)
    out["ceiling"] = out["p99"]

    if ownership is None:
        out["ownership"] = np.nan
    else:
        mapping = dict(ownership) if not isinstance(ownership, pd.Series) else ownership.to_dict()
        out["ownership"] = [
            float(mapping[p]) if p in mapping and pd.notna(mapping[p]) else np.nan
            for p in out["player"]
        ]
    out["leverage_score"] = leverage_score(out["projection"], out["ownership"])
    out["stack_rating"] = stack_rating(out.rename(columns={"projection": "points"}))

    out = round_columns(out, ("projection", "median", "p75", "p90", "p99", "ceiling"), 2)
    out = round_columns(out, ("value_score",), 2)
    out = round_columns(out, ("ownership",), 3)
    return out


def build_dfs(
    pool: pd.DataFrame | None,
    distribution: pd.DataFrame | None = None,
    *,
    ownership: Mapping[str, float] | pd.Series | None = None,
) -> pd.DataFrame:
    """The DFS pool table, before metadata is stamped on it."""
    base = [c for c in DFS_COLUMNS if c not in ("generated_at", "season", "week")]
    spine = _base_pool(pool, distribution, ownership)
    if spine.empty:
        return pd.DataFrame(columns=base)
    return spine.reindex(columns=base)


def build_dfs_optimizer(
    pool: pd.DataFrame | None,
    distribution: pd.DataFrame | None = None,
    *,
    ownership: Mapping[str, float] | pd.Series | None = None,
) -> pd.DataFrame:
    """The optimizer table: one row per player, one column per contest type."""
    base = [c for c in DFS_OPTIMIZER_COLUMNS if c not in ("generated_at", "season", "week")]
    spine = _base_pool(pool, distribution, ownership)
    if spine.empty:
        return pd.DataFrame(columns=base)
    for column, quantile in CONTEST_QUANTILES:
        spine[column] = spine[quantile]
    return spine.reindex(columns=base)


def export_dfs(
    meta: ExportMeta,
    pool: pd.DataFrame | None,
    distribution: pd.DataFrame | None = None,
    *,
    out_dir: str | Path = EXPORT_DIR,
    ownership: Mapping[str, float] | pd.Series | None = None,
) -> tuple[Path, Path]:
    """Write ``dfs.csv`` and ``dfs_optimizer.csv``. Returns both paths."""
    folder = Path(out_dir)
    pool_path = write_csv(
        build_dfs(pool, distribution, ownership=ownership),
        folder / "dfs.csv", DFS_COLUMNS, meta,
    )
    opt_path = write_csv(
        build_dfs_optimizer(pool, distribution, ownership=ownership),
        folder / "dfs_optimizer.csv", DFS_OPTIMIZER_COLUMNS, meta,
    )
    return pool_path, opt_path
