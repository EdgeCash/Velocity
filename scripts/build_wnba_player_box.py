"""Build the WNBA player-box dataset from the wehoop release parquets.

The team box (``build_wnba_box.py``) carries four possession columns and
nothing about who was on the floor, which is why the repo could price a WNBA
game and not a WNBA lineup. This is the sibling release on the same transport
— one parquet per season, reachable from CI, where ESPN's own edge 403s
datacenter IPs — carrying the full DraftKings scoring line per player-game.

Only the columns a projection and a scorer need are kept, so the frame commits
like every other ``datasets/`` file. ``minutes`` and ``starter`` are what make
the rate model possible: DK pays production, and production is a rate times
the minutes a coach gives.

    python scripts/build_wnba_player_box.py --seasons 2024 2025 2026
"""

from __future__ import annotations

import argparse
import io
import time
import urllib.request
from pathlib import Path

import pandas as pd

_BOX_URL = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/"
            "download/espn_wnba_player_boxscores/player_box_{season}.parquet")
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) velocity-datasets"

# Identity, context, and every stat DraftKings scores.
_IDENTITY = ["game_id", "game_date", "athlete_id", "athlete_display_name",
             "athlete_position_abbreviation", "team_abbreviation",
             "opponent_team_abbreviation", "home_away", "season", "season_type",
             "starter", "did_not_play"]
_STATS = ["minutes", "points", "rebounds", "assists", "steals", "blocks",
          "turnovers", "three_point_field_goals_made"]


def slim_player_box(raw: pd.DataFrame, season: int) -> pd.DataFrame:
    """Identity + the DK scoring line, numerics coerced (pure, tested).

    A column the release does not ship contributes nulls rather than raising,
    so an older season with a thinner schema still banks.
    """
    out = pd.DataFrame(index=raw.index)
    for col in _IDENTITY:
        out[col] = raw[col] if col in raw.columns else pd.NA
    for col in _STATS:
        out[col] = pd.to_numeric(raw[col], errors="coerce") if col in raw.columns else float("nan")
    out["game_id"] = out["game_id"].astype(str)
    out["athlete_id"] = out["athlete_id"].astype(str)
    out["game_date"] = pd.to_datetime(out["game_date"], errors="coerce")
    for flag in ("starter", "did_not_play"):
        out[flag] = out[flag].fillna(False).astype(bool)
    out["season"] = season
    # A player who did not appear has no rate to contribute; keeping his zero
    # would drag every per-minute estimate toward zero for the wrong reason.
    out = out[~out["did_not_play"]]
    return out[[*_IDENTITY, *_STATS]].reset_index(drop=True)


def fetch_player_box(season: int) -> pd.DataFrame:  # pragma: no cover - network
    req = urllib.request.Request(_BOX_URL.format(season=season),
                                 headers={"User-Agent": _USER_AGENT})
    for attempt, delay in enumerate((0, 5, 15)):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                return slim_player_box(pd.read_parquet(io.BytesIO(resp.read())), season)
        except Exception:  # noqa: BLE001 - retried; the last attempt raises
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")


def bank_player_boxes(
    seasons: list[int], out_path: str | Path
) -> int:  # pragma: no cover - network orchestration
    """Refresh ``seasons`` in the committed frame, keep other seasons as-is."""
    out_path = Path(out_path)
    existing = pd.read_parquet(out_path) if out_path.exists() else pd.DataFrame()
    fresh = pd.concat([fetch_player_box(s) for s in seasons], ignore_index=True)
    if not existing.empty:
        keep = existing[~existing["season"].isin(seasons)]
        fresh = pd.concat([keep, fresh], ignore_index=True)
    fresh = fresh.sort_values(["season", "game_id", "athlete_id"]).reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fresh.to_parquet(out_path, index=False)
    print(f"wrote {len(fresh)} player-box rows "
          f"({fresh['game_id'].nunique()} games, "
          f"{fresh['athlete_id'].nunique()} players) to {out_path}")
    return len(fresh)


def main() -> None:  # pragma: no cover - network orchestration
    parser = argparse.ArgumentParser(description="Bank WNBA player boxes")
    parser.add_argument("--seasons", type=int, nargs="+", default=[2024, 2025, 2026])
    parser.add_argument("--out", default="datasets/wnba/player_box.parquet")
    args = parser.parse_args()
    bank_player_boxes(args.seasons, args.out)


if __name__ == "__main__":
    main()
