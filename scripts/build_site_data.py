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
import shutil
from pathlib import Path

import pandas as pd

LEAGUES = ("nfl", "ncaaf", "mlb", "wnba", "ncaab")
_STAMP = r"(\d{8}T\d{6}Z)"

# Evidence's source runner writes no parquet at all for a query that returns
# zero rows, and the build then dies reading the missing extraction ("too
# small to be a Parquet file"). So an absent family ships exactly one
# sentinel row, and every page query filters `league != '__none__'`.
SENTINEL_LEAGUE = "__none__"

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
    # Real graded frames carry profit as object dtype (pending rows mix None
    # in upstream) — coerce before any cython op.
    settled["profit"] = pd.to_numeric(settled["profit"], errors="coerce").fillna(0.0)
    settled["slate_date"] = pd.to_datetime(settled["slate_date"]).dt.date
    daily = (settled.groupby(["league", "slate_date"], as_index=False)
             .agg(profit=("profit", "sum"), bets=("profit", "size")))
    daily = daily.sort_values(["league", "slate_date"])
    daily["units"] = daily.groupby("league")["profit"].cumsum()
    return daily


# The rendered card families (velocity.report.*_png). Matchup-keyed kinds
# carry `_{AWAY}_at_{HOME}` in the filename; recordcard is one per league.
CARD_KINDS = ("social", "deepdive", "simcheck", "recordcard")


def _card_captions(folder: Path, stem: str) -> dict[str, str]:
    """``{kind}_{league}_{stamp}_captions.md`` parsed into AWAY @ HOME → text."""
    path = next(iter(folder.rglob(f"{stem}_captions.md")), None)
    if path is None:
        return {}
    captions: dict[str, str] = {}
    for block in path.read_text().split("\n---\n"):
        block = block.strip()
        head = block.split(" — ", 1)[0].strip()
        if head:
            captions[head] = block
    return captions


def collect_cards(slate_dir: Path, static_out: Path) -> pd.DataFrame:
    """Copy the newest-stamp card PNGs per (kind, league) into the site.

    The PNGs land in ``static_out`` (served at ``/cards/<name>``) and the
    returned manifest gives the Graphics page its gallery rows, with the
    social caption attached where the captions file carries the matchup.
    """
    static_out.mkdir(parents=True, exist_ok=True)
    for stale in static_out.glob("*.png"):
        stale.unlink()
    rows = []
    for kind in CARD_KINDS:
        for league in LEAGUES:
            pattern = rf"{kind}_{league}_{_STAMP}(_.+)?\.png"
            found = sorted(
                {p.name: p for p in slate_dir.rglob("*.png")
                 if re.fullmatch(pattern, p.name)}.values(),
                key=lambda p: p.name,
            )
            if not found:
                continue
            stamp_match = re.search(_STAMP, found[-1].name)
            stamp = stamp_match.group(1) if stamp_match else ""
            batch = [p for p in found if stamp in p.name]
            captions = _card_captions(slate_dir, f"{kind}_{league}_{stamp}")
            for path in batch:
                match = re.fullmatch(
                    rf"{kind}_{league}_{stamp}_(.+)_at_(.+)\.png", path.name)
                away, home = match.groups() if match else ("", "")
                shutil.copy2(path, static_out / path.name)
                rows.append({
                    "kind": kind, "league": league, "stamp": stamp,
                    "file": path.name, "away": away, "home": home,
                    "caption": captions.get(f"{away} @ {home}", ""),
                })
    return pd.DataFrame(rows)


def sentinel_frame(schema: dict[str, object]) -> pd.DataFrame:
    """One filterable placeholder row matching ``schema``.

    Dates get a real timestamp (Evidence downcasts all-null date columns to
    Float64, which would change the extracted column type); everything else
    is inert. The ``league`` column always exists and carries the marker.
    """
    def value(col: str, dtype: object) -> object:
        if col == "league":
            return SENTINEL_LEAGUE
        if dtype is str:
            return ""
        if dtype is int:
            return 0
        if dtype is float:
            return float("nan")
        return pd.Timestamp("2000-01-01")

    row = {col: value(col, dtype) for col, dtype in schema.items()}
    return pd.DataFrame([row]).astype(schema)  # type: ignore[arg-type]


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
    parser.add_argument("--cards-out", default="site/static/cards")
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
        "cards": collect_cards(slate_dir, Path(args.cards_out)),
    }
    tables["units"] = build_units(tables["cumulative_record"]
                                  if not tables["cumulative_record"].empty
                                  else tables["record"])

    # An absent family still writes a typed one-row sentinel frame so every
    # page's SQL parses AND every source query returns a row (see
    # SENTINEL_LEAGUE); the pages filter the sentinel and render empty states.
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
        "cards": {"kind": str, "league": str, "stamp": str, "file": str,
                  "away": str, "home": str, "caption": str},
        "units": {"league": str, "slate_date": "datetime64[ns]",
                  "profit": float, "bets": int, "units": float},
    }
    for name, frame in tables.items():
        if frame.empty:
            frame = sentinel_frame(schemas[name])
        frame.to_parquet(out / f"{name}.parquet", index=False)
        print(f"{name}: {len(frame)} rows")


if __name__ == "__main__":
    main()
