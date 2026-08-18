"""Join banked historical closes onto a games frame — the summer lab unlock.

The walk-forward lab's market benchmarks (ATS vs close, disagreement sweeps,
the market-blend ceiling) need per-game closing ``spread_line``/``total_line``.
The football frames carry them from free feeds; MLB/WNBA closes come from The
Odds API's historical archive, banked by ``collect_historical_odds.py`` as
**private** artifacts. This script joins them:

1. read every ``lines_<league>_*.parquet`` + ``events_<league>_*.parquet``
   under ``--archive`` (a downloaded artifact folder),
2. per provider game, keep the **last snapshot before kickoff** and reduce
   each market to a consensus (median across books): ``spread_line``
   (positive = home favored, the datasets' convention) and ``total_line``,
3. match provider games onto the committed games frame by exact team names
   (they agree by construction) and **nearest kickoff**, doubleheader-safe,
4. write the augmented games parquet to ``--out``.

The output contains paid line data, so it must never land in the public repo:
``--out`` defaults under ``artifacts/`` (gitignored) and the lab is pointed at
it with ``--data``. Only aggregated lab verdicts are committed.

    python scripts/join_historical_closes.py --league mlb \
        --archive artifacts/hist --games datasets/mlb/games.parquet \
        --out artifacts/lab/mlb
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

_STAMP = re.compile(r"(lines|events)_([a-z]+)_(\d{8}T\d{6}Z?)\.parquet$")


def load_archive(archive: Path, league: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Concatenate every banked lines/events parquet for ``league``."""
    lines_frames, event_frames = [], []
    for path in sorted(archive.rglob("*.parquet")):
        match = _STAMP.search(path.name)
        if match is None or match.group(2) != league:
            continue
        frame = pd.read_parquet(path)
        (lines_frames if match.group(1) == "lines" else event_frames).append(frame)
    lines = pd.concat(lines_frames, ignore_index=True) if lines_frames else pd.DataFrame()
    events = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame()
    if not events.empty:
        events = events.drop_duplicates(subset="game_id", keep="last")
    return lines, events


def closing_consensus(lines: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Per provider game: consensus close from the last pre-kickoff snapshot.

    Returns ``game_id, home_team, away_team, kickoff, spread_line, total_line``.
    ``spread_line`` is positive when home is favored (the datasets' convention;
    the canonical ``Lines`` home spread point is the home handicap, so the sign
    flips). Median across books at the chosen snapshot — one book's stale
    number never becomes "the close".
    """
    if lines.empty or events.empty:
        return pd.DataFrame(columns=["game_id", "home_team", "away_team",
                                     "kickoff", "spread_line", "total_line"])
    kick = events.set_index("game_id")["kickoff"].map(pd.Timestamp).to_dict()
    df = lines.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["_kick"] = df["game_id"].map(kick)
    df = df[df["_kick"].notna() & (df["timestamp"] <= df["_kick"])]
    if df.empty:
        return pd.DataFrame(columns=["game_id", "home_team", "away_team",
                                     "kickoff", "spread_line", "total_line"])
    # The last snapshot per game, then medians within it.
    last = df.groupby("game_id")["timestamp"].transform("max")
    df = df[df["timestamp"] == last]
    home_by_game = events.set_index("game_id")["home_team"].to_dict()
    rows = []
    for game_id, group in df.groupby("game_id"):
        # The canonical Lines frame names sides by TEAM for spreads (and
        # Over/Under capitalized for totals) — resolve via the event's teams.
        home_name = str(home_by_game.get(game_id, ""))
        spread_home = group[(group["market"] == "spread")
                            & (group["side"].astype(str) == home_name)]
        total_over = group[(group["market"] == "total")
                           & (group["side"].astype(str).str.lower() == "over")]
        spread = -float(spread_home["point"].median()) if len(spread_home) else None
        total = float(total_over["point"].median()) if len(total_over) else None
        event = events[events["game_id"] == game_id].iloc[0]
        rows.append({
            "game_id": str(game_id),
            "home_team": event["home_team"],
            "away_team": event["away_team"],
            "kickoff": pd.Timestamp(event["kickoff"]),
            "spread_line": spread,
            "total_line": total,
        })
    return pd.DataFrame(rows)


def join_closes(
    games: pd.DataFrame, closes: pd.DataFrame, *, tolerance_hours: float = 6.0
) -> pd.DataFrame:
    """Attach ``spread_line``/``total_line`` to ``games`` by teams + nearest kickoff.

    Team names agree exactly by construction (both sides use the providers'
    full names). Kickoff matching is nearest-within-tolerance and **one-shot**
    per provider game, so an MLB doubleheader's two games each get their own
    close (or none) instead of sharing one.
    """
    out = games.copy()
    out["spread_line"] = pd.Series(pd.NA, index=out.index, dtype="Float64")
    out["total_line"] = pd.Series(pd.NA, index=out.index, dtype="Float64")
    if closes.empty:
        return out
    tol = pd.Timedelta(hours=tolerance_hours)
    used: set[str] = set()
    closes = closes.assign(_kick=pd.to_datetime(closes["kickoff"]))
    grouped = dict(iter(closes.groupby(["home_team", "away_team"])))
    kick = pd.to_datetime(out["kickoff"])
    for idx in out.sort_values("kickoff").index:
        key = (out.at[idx, "home_team"], out.at[idx, "away_team"])
        group = grouped.get(key)
        if group is None:
            continue
        candidates = group[~group["game_id"].isin(used)]
        if candidates.empty:
            continue
        deltas = (candidates["_kick"] - kick[idx]).abs()
        best = deltas.idxmin()
        if deltas[best] > tol:
            continue
        used.add(str(candidates.at[best, "game_id"]))
        spread = candidates.at[best, "spread_line"]
        total = candidates.at[best, "total_line"]
        if pd.notna(spread):
            out.at[idx, "spread_line"] = float(spread)
        if pd.notna(total):
            out.at[idx, "total_line"] = float(total)
    return out


def main() -> None:  # pragma: no cover - file orchestration
    parser = argparse.ArgumentParser(description="Join banked closes onto games")
    parser.add_argument("--league", required=True, choices=["mlb", "wnba"])
    parser.add_argument("--archive", required=True,
                        help="folder of downloaded hist artifacts (private)")
    parser.add_argument("--games", required=True, help="committed games parquet")
    parser.add_argument("--out", required=True,
                        help="output folder (private — paid lines, never git)")
    args = parser.parse_args()

    lines, events = load_archive(Path(args.archive), args.league)
    print(f"archive: {len(lines)} line rows, {len(events)} events")
    closes = closing_consensus(lines, events)
    games = pd.read_parquet(args.games)
    joined = join_closes(games, closes)
    n_spread = int(joined["spread_line"].notna().sum())
    n_total = int(joined["total_line"].notna().sum())
    print(f"joined closes onto {len(joined)} games: "
          f"{n_spread} spreads ({n_spread / max(len(joined), 1):.0%}), "
          f"{n_total} totals")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "games.parquet"
    joined.to_parquet(dest, index=False)
    print(f"wrote {dest} (private path — do not commit)")


if __name__ == "__main__":
    main()
