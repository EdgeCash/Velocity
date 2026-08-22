"""Assemble the Evidence site's data dir from a slate-artifact folder.

The site (``site/``, docs/DASHBOARD_RESEARCH.md §6) is a static Evidence
build over parquet. This script is the seam between the pipeline's
stamped artifact families and the site's stable table names: it finds the
**latest stamp per league** for each frame kind, joins what the pages
need pre-joined, and writes one parquet per table into
``site/sources/velocity/data/``.

    python scripts/build_site_data.py --slate-dir artifacts/slate

Everything is best-effort per league: a league with no artifacts simply
contributes no rows. The output dir is gitignored — slate frames are
paid-odds-derived and never enter git; the built site deploys only to the
Access-gated private host (public split is a later phase).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

LEAGUES = ("nfl", "ncaaf", "mlb", "wnba", "ncaab")
_STAMP = r"(\d{8}T\d{6}Z)"

# The "what's live" transparency block, mirrored from the plays app.
from sys import path as _sys_path  # noqa: E402

_sys_path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))


def newest(folder: Path, pattern: str) -> Path | None:
    """Lexicographically-last match — the stamp format sorts chronologically."""
    matches = sorted(p for p in folder.rglob("*") if re.fullmatch(pattern, p.name))
    return matches[-1] if matches else None


def latest_frame(folder: Path, prefix: str, league: str) -> pd.DataFrame | None:
    """The newest ``{prefix}_{stamp}.parquet`` under ``folder``, or None.

    ``{league}`` in the prefix is substituted, so ``slate_{league}_props``
    finds the props family and ``record_{league}`` the record family.
    """
    stem = prefix.format(league=league)
    path = newest(folder, rf"{re.escape(stem)}_{_STAMP}\.parquet")
    if path is None:
        return None
    frame = pd.read_parquet(path)
    stamp = re.search(_STAMP, path.name)
    frame["league"] = league
    frame["stamp"] = stamp.group(1) if stamp else ""
    return frame


def collect(folder: Path, kind: str) -> pd.DataFrame:
    """Latest frame per league for ``kind``, concatenated (empty if none).

    ``kind`` is either a bare family (``record`` → ``record_{league}``) or a
    prefix template containing ``{league}``.
    """
    prefix = kind if "{league}" in kind else kind + "_{league}"
    frames = [f for lg in LEAGUES if (f := latest_frame(folder, prefix, lg)) is not None]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_board(slate_dir: Path) -> pd.DataFrame:
    """The Today board: slate rows joined with games and projections."""
    slate = collect(slate_dir, "slate")
    games = collect(slate_dir, "games")
    projections = collect(slate_dir, "projections")
    intel = collect(slate_dir, "intel")
    if slate.empty or games.empty:
        return pd.DataFrame()
    board = slate.merge(
        games[["game_id", "home_team", "away_team", "kickoff"]].drop_duplicates("game_id"),
        on="game_id", how="left",
    )
    if not projections.empty:
        board = board.merge(
            projections[["game_id", "p_home_win", "mu_home", "mu_away",
                         "fair_spread", "fair_total"]].drop_duplicates("game_id"),
            on="game_id", how="left",
        )
    if not intel.empty and "tier" in intel.columns:
        keys = ["game_id", "market", "side"]
        tiers = intel.drop_duplicates(subset=keys)[[*keys, "tier", "conviction"]]
        board = board.merge(tiers, on=keys, how="left")
    return board


def build_units(record: pd.DataFrame) -> pd.DataFrame:
    """Per-day settled profit and running units per league, for the record chart."""
    if record.empty or "result" not in record.columns:
        return pd.DataFrame()
    settled = record[record["result"].isin(["win", "loss", "push"])].copy()
    if settled.empty:
        return pd.DataFrame()
    settled["slate_date"] = pd.to_datetime(settled["slate_date"]).dt.date
    daily = (settled.groupby(["league", "slate_date"], as_index=False)
             .agg(profit=("profit", "sum"), bets=("profit", "size")))
    daily = daily.sort_values(["league", "slate_date"])
    daily["units"] = daily.groupby("league")["profit"].cumsum()
    return daily


def model_config_frame() -> pd.DataFrame:
    """The per-league "what's live" block, re-exported from the plays app."""
    try:
        from format_plays import MODEL_CONFIG  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - the site renders without the block
        return pd.DataFrame()
    rows = []
    for league, entries in MODEL_CONFIG.items():
        for label, detail in entries:
            rows.append({"league": league, "label": label, "detail": detail})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble the site's data dir")
    parser.add_argument("--slate-dir", default="artifacts/slate")
    parser.add_argument("--out", default="site/sources/velocity/data")
    args = parser.parse_args()

    slate_dir = Path(args.slate_dir)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tables: dict[str, pd.DataFrame] = {
        "board": build_board(slate_dir),
        "games": collect(slate_dir, "games"),
        "projections": collect(slate_dir, "projections"),
        "distributions": collect(slate_dir, "distributions"),
        "record": collect(slate_dir, "record"),
        "cumulative_record": collect(slate_dir, "cumulative_record"),
        "props": collect(slate_dir, "slate_{league}_props"),
        "dfs_lineup": collect(slate_dir, "dfs_lineup"),
        "dfs_gpp": collect(slate_dir, "dfs_gpp"),
        "portfolio": collect(slate_dir, "portfolio"),
        "model_config": model_config_frame(),
    }
    tables["units"] = build_units(tables["cumulative_record"]
                                  if not tables["cumulative_record"].empty
                                  else tables["record"])

    # An absent family still writes a typed empty frame so every page's SQL
    # parses; the pages just render their empty states.
    schemas: dict[str, dict[str, object]] = {
        "board": {"game_id": str, "market": str, "side": str, "point": float,
                  "book": str, "price": float, "p_model": float, "p_fair": float,
                  "edge": float, "stake": float, "league": str, "stamp": str,
                  "home_team": str, "away_team": str,
                  "kickoff": "datetime64[ns]", "p_home_win": float,
                  "mu_home": float, "mu_away": float, "fair_spread": float,
                  "fair_total": float, "tier": str, "conviction": float},
        "games": {"game_id": str, "home_team": str, "away_team": str,
                  "kickoff": "datetime64[ns]", "league": str, "stamp": str},
        "projections": {"game_id": str, "away": str, "home": str, "n_sims": int,
                        "mu_away": float, "mu_home": float, "p_home_win": float,
                        "fair_spread": float, "fair_total": float,
                        "league": str, "stamp": str},
        "distributions": {"game_id": str, "kind": str, "value": float,
                          "prob": float, "league": str, "stamp": str},
        "record": {"section": str, "play": str, "market": str, "side": str,
                   "point": float, "price": float, "stake": float,
                   "result": str, "profit": float, "slate_date": "datetime64[ns]",
                   "league": str, "stamp": str},
        "cumulative_record": {"section": str, "play": str, "market": str,
                              "side": str, "point": float, "price": float,
                              "stake": float, "result": str, "profit": float,
                              "slate_date": "datetime64[ns]", "league": str,
                              "stamp": str},
        "props": {"game_id": str, "player": str, "market": str, "side": str,
                  "point": float, "price": float, "p_model": float,
                  "p_fair": float, "edge": float, "stake": float,
                  "league": str, "stamp": str},
        "dfs_lineup": {"slot": str, "player_name": str, "position": str,
                       "team": str, "salary": float, "points": float,
                       "league": str, "stamp": str},
        "dfs_gpp": {"rank": int, "players": str, "total_salary": float,
                    "total_points": float, "score": float, "stacks": str,
                    "league": str, "stamp": str},
        "portfolio": {"game_id": str, "market": str, "side": str, "kind": str,
                      "price": float, "edge": float, "stake": float,
                      "stake_solo": float, "league": str, "stamp": str},
        "model_config": {"league": str, "label": str, "detail": str},
        "units": {"league": str, "slate_date": "datetime64[ns]",
                  "profit": float, "bets": int, "units": float},
    }
    for name, frame in tables.items():
        if frame.empty:
            frame = pd.DataFrame({col: pd.Series(dtype=dtype)  # type: ignore[arg-type]
                                  for col, dtype in schemas[name].items()})
        frame.to_parquet(out / f"{name}.parquet", index=False)
        print(f"{name}: {len(frame)} rows")


if __name__ == "__main__":
    main()
