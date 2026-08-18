"""Build the WNBA team-box dataset from the wehoop release parquets.

The pace×efficiency model (docs/MODEL_LAB.md WNBA Round 2) needs each game's
possession components. ESPN's own edge 403s datacenter IPs, so the transport
is the proven wehoop release-asset pattern (one parquet per season, always
reachable from CI). Only the possession columns are kept — the slim frame
commits like every ``datasets/`` file.

    python scripts/build_wnba_box.py --seasons 2024 2025 2026
"""

from __future__ import annotations

import argparse
import io
import time
import urllib.request
from pathlib import Path

import pandas as pd

_BOX_URL = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/"
            "download/espn_wnba_team_boxscores/team_box_{season}.parquet")
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) velocity-datasets"
_KEEP = ["game_id", "team_home_away", "field_goals_attempted",
         "offensive_rebounds", "total_turnovers", "free_throws_attempted"]


def slim_team_box(raw: pd.DataFrame, season: int) -> pd.DataFrame:
    """Keep the possession components only, numerics coerced (pure, tested)."""
    out = raw[_KEEP].copy()
    out["game_id"] = out["game_id"].astype(str)
    out["team_home_away"] = out["team_home_away"].astype(str)
    for col in _KEEP[2:]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["season"] = season
    return out


def fetch_team_box(season: int) -> pd.DataFrame:  # pragma: no cover - network
    req = urllib.request.Request(_BOX_URL.format(season=season),
                                 headers={"User-Agent": _USER_AGENT})
    for attempt, delay in enumerate((0, 5, 15)):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                return slim_team_box(pd.read_parquet(io.BytesIO(resp.read())), season)
        except Exception:  # noqa: BLE001 - retried; the last attempt raises
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")


def bank_team_boxes(
    seasons: list[int], out_path: str | Path
) -> int:  # pragma: no cover - network orchestration
    """Refresh ``seasons`` in the committed frame, keep other seasons as-is."""
    out_path = Path(out_path)
    existing = pd.read_parquet(out_path) if out_path.exists() else pd.DataFrame()
    fresh = pd.concat([fetch_team_box(s) for s in seasons], ignore_index=True)
    if not existing.empty:
        keep = existing[~existing["season"].isin(seasons)]
        fresh = pd.concat([keep, fresh], ignore_index=True)
    fresh = fresh.sort_values(["season", "game_id"]).reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fresh.to_parquet(out_path, index=False)
    print(f"wrote {len(fresh)} team-box rows "
          f"({fresh['game_id'].nunique()} games) to {out_path}")
    return len(fresh)


def main() -> None:  # pragma: no cover - network orchestration
    parser = argparse.ArgumentParser(description="Bank WNBA team boxes")
    parser.add_argument("--seasons", type=int, nargs="+",
                        default=[2024, 2025, 2026])
    parser.add_argument("--out", default="datasets/wnba/team_box.parquet")
    args = parser.parse_args()
    bank_team_boxes(args.seasons, args.out)


if __name__ == "__main__":
    main()
