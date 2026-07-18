"""Generator for the frozen synthetic play-by-play fixture.

The offline test suite must not hit the network, so we ship a small, frozen
play-by-play sample. Rather than hand-type thousands of rows, we generate them
from *known* per-team true strengths under the exact model the ratings fitter
assumes::

    epa  ~  Normal(league_mean + true_off[posteam] + true_def[defteam],  sd)

Because the truth is known, tests can assert the fitter *recovers* it (ranking
and sign), which is a far stronger check than pinning arbitrary numbers. The
output is deterministic (fixed seed) and committed as ``nfl_plays.csv``; this
script exists so the fixture is reproducible and its provenance is auditable.

Run from the repo root to regenerate::

    python tests/fixtures/_generate.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20_230_907
LEAGUE_MEAN_EPA = -0.02
PLAY_EPA_SD = 1.35
PLAYS_PER_SIDE = 40
SEASON = 2023

# (team, true offensive EPA/play deviation, true defensive EPA/play allowed).
# Lower defense = better defense. Ordered best→worst offense for readability.
TRUE_STRENGTHS: dict[str, tuple[float, float]] = {
    "KC": (0.15, -0.05),
    "BUF": (0.10, -0.08),
    "SF": (0.05, -0.12),
    "DAL": (0.03, -0.03),
    "DET": (0.00, 0.02),
    "JAX": (-0.05, 0.03),
    "CHI": (-0.10, 0.06),
    "CAR": (-0.15, 0.10),
}


def generate_plays() -> pd.DataFrame:
    """Build the frozen play-by-play frame (matches store.schema.Plays)."""
    rng = np.random.default_rng(SEED)
    teams = list(TRUE_STRENGTHS)
    off = {t: TRUE_STRENGTHS[t][0] for t in teams}
    deff = {t: TRUE_STRENGTHS[t][1] for t in teams}

    records: list[dict[str, object]] = []
    week = 0
    # Double round-robin: every ordered pair plays once (home/away both ways).
    for home in teams:
        for away in teams:
            if home == away:
                continue
            week = week % 18 + 1
            game_id = f"{SEASON}_{week:02d}_{away}_{home}"
            for posteam, defteam in ((home, away), (away, home)):
                mean_epa = LEAGUE_MEAN_EPA + off[posteam] + deff[defteam]
                epa = rng.normal(mean_epa, PLAY_EPA_SD, size=PLAYS_PER_SIDE)
                downs = rng.integers(1, 5, size=PLAYS_PER_SIDE)
                is_pass = rng.random(size=PLAYS_PER_SIDE) < 0.58
                yards = np.rint(epa * 5.0 + rng.normal(0, 3, size=PLAYS_PER_SIDE))
                for i in range(PLAYS_PER_SIDE):
                    records.append(
                        {
                            "play_id": f"{game_id}_{posteam}_{i:03d}",
                            "game_id": game_id,
                            "season": SEASON,
                            "week": int(week),
                            "posteam": posteam,
                            "defteam": defteam,
                            "play_type": "pass" if is_pass[i] else "run",
                            "down": float(downs[i]),
                            "yards_gained": float(yards[i]),
                            "epa": float(epa[i]),
                            "success": bool(epa[i] > 0),
                        }
                    )

    return pd.DataFrame.from_records(records)


def main() -> None:
    df = generate_plays()
    out = Path(__file__).parent / "nfl_plays.csv"
    df.to_csv(out, index=False)
    print(f"wrote {len(df)} plays for {df['game_id'].nunique()} games -> {out}")


if __name__ == "__main__":
    main()
