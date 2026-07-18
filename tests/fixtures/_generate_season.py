"""Generator for the frozen backtest season (games + two-sided line archive).

The walk-forward backtest needs three consistent frozen frames: the plays
(already in ``nfl_plays.csv``), the games with **final scores**, and a **line
archive**. This script produces the latter two from the same known team
strengths that generated the plays, so the whole season is internally coherent.

Two design choices make the backtest meaningful:

* **Scores** are drawn from the true strengths under the same base-points × pace
  scoring model the projection model assumes, so a model that recovers the
  ratings is (approximately) calibrated against them.
* **Lines** are generated as an *opening* number that is off the sharp value plus
  a *closing* number that has moved toward it. A model that is near the truth
  therefore tends to sit on the side the market closes toward — i.e. it earns
  positive closing-line value — which is exactly the acceptance signal the
  backtest exists to measure.

The game list (ids, weeks, home/away) is read straight from ``nfl_plays.csv`` so
it cannot drift from the plays. A dedicated RNG (seed distinct from the plays
generator) means regenerating this file never perturbs ``nfl_plays.csv``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from tests.fixtures._generate import TRUE_STRENGTHS

SEED = 424_242
BASE_POINTS = 22.5
PLAYS_PER_GAME = 63.0
HFA_POINTS = 2.0
SCORE_SD = 9.0
MARGIN_SD = 13.5  # for converting a margin into a win probability
SEASON_START = pd.Timestamp("2023-09-07 20:00:00")

FIXTURES = Path(__file__).parent


def _round_half(x: float) -> float:
    return round(x * 2.0) / 2.0


def _erf(x: float) -> float:
    import math

    return math.erf(x)


def _american_from_prob(q: float) -> int:
    """American odds from a vig-inclusive implied probability ``q``."""
    q = min(max(q, 0.02), 0.98)
    decimal = 1.0 / q
    american = (decimal - 1.0) * 100.0 if decimal >= 2.0 else -100.0 / (decimal - 1.0)
    return int(round(american))


def _game_rows() -> list[dict[str, object]]:
    plays = pd.read_csv(FIXTURES / "nfl_plays.csv")
    rows = []
    for game_id in plays["game_id"].drop_duplicates():
        season, week, away, home = game_id.split("_")
        rows.append(
            {
                "game_id": game_id,
                "season": int(season),
                "week": int(week),
                "home": home,
                "away": away,
            }
        )
    return rows


def _expected_points(home: str, away: str) -> tuple[float, float]:
    off_h, def_h = TRUE_STRENGTHS[home]
    off_a, def_a = TRUE_STRENGTHS[away]
    mu_home = BASE_POINTS + PLAYS_PER_GAME * (off_h + def_a) + HFA_POINTS / 2.0
    mu_away = BASE_POINTS + PLAYS_PER_GAME * (off_a + def_h) - HFA_POINTS / 2.0
    return mu_home, mu_away


def generate() -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    games: list[dict[str, object]] = []
    lines: list[dict[str, object]] = []
    line_id = 0

    for row in _game_rows():
        gid, week, home, away = row["game_id"], row["week"], row["home"], row["away"]
        kickoff = SEASON_START + pd.Timedelta(days=7 * (int(week) - 1))
        mu_home, mu_away = _expected_points(str(home), str(away))

        home_score = max(0, int(round(rng.normal(mu_home, SCORE_SD))))
        away_score = max(0, int(round(rng.normal(mu_away, SCORE_SD))))
        games.append(
            {
                "game_id": gid,
                "league": "nfl",
                "season": row["season"],
                "week": week,
                "season_type": "REG",
                "kickoff": kickoff,
                "home_team": home,
                "away_team": away,
                "neutral_site": False,
                "roof": "outdoors",
                "surface": "grass",
                "home_score": home_score,
                "away_score": away_score,
            }
        )

        sharp_margin = mu_home - mu_away
        sharp_total = mu_home + mu_away
        p_home = float(_erf((sharp_margin / MARGIN_SD) / np.sqrt(2.0)) * 0.5 + 0.5)

        # Opening number is off the sharp value; closing has moved toward it.
        for phase, ts_offset, err_spread, err_total, err_p in (
            ("open", pd.Timedelta(days=-3), 3.0, 4.0, 0.05),
            ("close", pd.Timedelta(hours=-1), 1.0, 1.5, 0.02),
        ):
            ts = kickoff + ts_offset
            is_closing = phase == "close"
            spread_home = _round_half(-(sharp_margin + rng.normal(0.0, err_spread)))
            total_pt = _round_half(sharp_total + rng.normal(0.0, err_total))
            p_obs = min(max(p_home + rng.normal(0.0, err_p), 0.05), 0.95)

            for market, side, point, price in (
                ("spread", "home", spread_home, -110),
                ("spread", "away", -spread_home, -110),
                ("total", "over", total_pt, -110),
                ("total", "under", total_pt, -110),
                ("moneyline", "home", None, _american_from_prob(p_obs * 1.02)),
                ("moneyline", "away", None, _american_from_prob((1.0 - p_obs) * 1.02)),
            ):
                line_id += 1
                lines.append(
                    {
                        "line_id": f"S{line_id:05d}",
                        "game_id": gid,
                        "book": "bookA",
                        "market": market,
                        "side": side,
                        "price": price,
                        "point": point,
                        "timestamp": ts,
                        "is_closing": is_closing,
                    }
                )

    return pd.DataFrame(games), pd.DataFrame(lines)


def main() -> None:
    games, lines = generate()
    games.to_csv(FIXTURES / "nfl_season_games.csv", index=False)
    lines.to_csv(FIXTURES / "nfl_season_lines.csv", index=False)
    print(f"wrote {len(games)} games and {len(lines)} lines")


if __name__ == "__main__":
    main()
