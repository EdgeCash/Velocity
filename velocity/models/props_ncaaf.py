"""College player props — the football prop sim on the college player bank.

The repo bought the full NCAAF event-market prop board every live run and
could price none of it: :func:`scripts.run_live_slate._prop_slate` reads
FantasyPros, and the FantasyPros public API has **no college endpoint at
all**, so the league filter came back empty and the slate skipped every time
(audit finding 6). The lines were banked and never read.

The DFS board hit the identical wall and solved it with
``datasets/ncaaf/player_games.parquet``; this is the same move one vertical
over. Two pieces are needed and neither is a new model:

* **A projection in the provider's shape.** :func:`team_player_means` wants a
  long ``(player, stat, value)`` frame of per-game means — nothing downstream
  knows or cares that FantasyPros produced it. :func:`player_prop_means`
  emits that frame off the banked college games, over the same six-game
  recency window :mod:`velocity.models.dfs_ncaaf` swept walk-forward (a
  college roster turns over every August, so a player's own recent usage is
  worth more than his career).
* **Dispersion that is actually college's.** This is the part that would have
  gone wrong quietly. Re-fitting ``scripts/fit_prop_dispersion.py`` on the
  college bank says the team volume swing is roughly **twice** the NFL's:

  | | NFL | college |
  |---|---|---|
  | `pass_volume_sigma` | 0.118 | **0.242** |
  | `rush_volume_sigma` | 0.175 | **0.228** |
  | receptions φ (WR / RB) | 0.027 / 0.083 | 0.000 / 0.035 |
  | per-catch yards sd (WR / RB) | 10.57 / 7.56 | 11.66 / 9.80 |
  | rushing CV (RB / QB) | 0.72 / 0.87 | 0.72 / 0.80 |

  Which is a coherent story rather than noise: college blowouts, tempo and
  talent gaps move the whole team's pie far harder than the NFL's, while
  *conditional* on the pie a player's share is no noisier — the per-player
  numbers come in at or below the NFL's. Shipping ``FootballPropConfig()``
  here would have simulated distributions about half as wide as they are, and
  a too-narrow distribution does not fail loudly: it manufactures edge, on
  every market at once.

Pure functions of frames; offline-testable, no network.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from velocity.ingest.cfb_players import CFB_POSITIONS
from velocity.models.dfs_ncaaf import RECENT_GAMES, recent_games
from velocity.models.props_football import FootballPropConfig, load_rush_pool

NCAAF_PROP_RESIDUALS_PATH = Path("datasets/ncaaf/prop_residuals.parquet")

# Bank column → the FantasyPros stat key `FP_STAT_TO_MARKET` reads. Touchdowns
# go in as `rush_tds`/`rec_tds`, which `team_player_means` accumulates into a
# player's touchdown rate rather than a market of its own.
BANK_STAT_TO_FP = {
    "pass_yards": "pass_yds",
    "attempts": "pass_att",
    "interceptions": "pass_int",
    "rush_yards": "rush_yds",
    "carries": "rush_att",
    "receiving_yards": "rec_yds",
    "receptions": "rec_rec",
    "pass_tds": "pass_tds",
    "rush_tds": "rush_tds",
    "receiving_tds": "rec_tds",
}
# `pass_completions` is the eleventh football prop market and the one absent
# here: cfbfastR records an incompletion's passer but not a completion count,
# so the bank carries attempts and yards and no completions. The market is
# left unpriced rather than inferred from a league-average completion rate.


def ncaaf_prop_config(n_sims: int = 10_000) -> FootballPropConfig:
    """:class:`FootballPropConfig` with the dispersion re-fitted on college.

    ``scripts/fit_prop_dispersion.py --league ncaaf`` produces every number
    below from ``datasets/ncaaf/player_games.parquet`` (2023-2026, 90,819
    player-games), by the same within-player-season decomposition used for the
    NFL. Positions the college bank has no sample for are **omitted** rather
    than entered as zero, so they fall back to the scalar default: DK lists
    college tight ends as receivers, so there are no TE rows to fit and a
    0.0 per-catch sd would have made one deterministic.
    """
    return FootballPropConfig(
        n_sims=n_sims,
        pass_volume_sigma=0.242,
        rush_volume_sigma=0.228,
        receptions_phi_by_position={"WR": 0.0, "RB": 0.035},
        receptions_phi=0.0,
        rush_attempts_phi_by_position={"RB": 0.0672, "QB": 0.0604},
        rush_attempts_phi=0.0672,
        pass_attempts_phi=0.0108,
        yards_sd_by_position={"WR": 11.66, "RB": 9.80},
        yards_sd_per_reception=11.66,
        rush_cv_by_position={"RB": 0.719, "QB": 0.802},
        rush_sd_fraction=0.719,
        rush_pool=load_rush_pool(NCAAF_PROP_RESIDUALS_PATH),
    )


def player_prop_means(
    player_games: pd.DataFrame,
    *,
    before: tuple[int, int] | None = None,
    window: int = RECENT_GAMES,
    active_season: int | None = None,
) -> pd.DataFrame:
    """Banked college player-games → the long per-stat frame the sim eats.

    Returns ``[player_id, player_name, team, position, stat, value]`` — the
    FantasyPros shape, so :func:`velocity.models.props_football.game_props`
    and :func:`name_index_from_fp` consume it with no idea it came from
    somewhere else.

    Each value is the player's mean over his ``window`` most recent games.
    Identity comes from the most recent of them, so a transfer carries the
    club he plays for now rather than the one he left.

    Two filters, both there to stop a line being priced on the wrong man:

    * **Only players active in ``active_season``** (the bank's newest by
      default). The window may still reach back into last season for a
      player's games — a college roster turns over every August and six games
      is worth more than one — but a player who has not taken a snap this
      season is not on a roster and is not projected off a two-year-old mean.
    * **No ambiguous names.** The provider's prop line carries a name and no
      id, so a name held by two banked players cannot be resolved, only
      guessed; ``name_index_from_fp`` would silently keep whichever came
      first. 180 names in the four banked seasons are held by more than one
      player and 20 of those are live in 2026, so both are dropped instead.
      Skipped and reported, never guessed — the same rule the prop slate
      already applies to a name it cannot resolve at all.

    ``before`` is ``(season, week)`` and is what keeps a backtest honest: a
    projection may only read games played before the one it is projecting.
    Live callers leave it off — the bank only holds games already played.
    """
    columns = ["player_id", "player_name", "team", "position", "stat", "value"]
    if player_games.empty:
        return pd.DataFrame(columns=columns)
    frame = player_games.copy()
    frame["player_id"] = frame["player_id"].astype(str)
    if before is not None:
        season, week = before
        frame = frame[(frame["season"] < season)
                      | ((frame["season"] == season) & (frame["week"] < week))]
    frame = frame[frame["position"].astype(str).isin(CFB_POSITIONS)]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    season_now = active_season if active_season is not None else int(frame["season"].max())
    active = set(frame.loc[frame["season"] == season_now, "player_id"])
    fitted = recent_games(frame, window)
    fitted = fitted[fitted["player_id"].isin(active)]
    if fitted.empty:
        return pd.DataFrame(columns=columns)

    latest = fitted.sort_values(["season", "week"]).drop_duplicates(
        "player_id", keep="last").set_index("player_id")
    holders = latest.reset_index().groupby("player_name")["player_id"].nunique()
    ambiguous = set(holders.index[holders > 1])
    latest = latest[~latest["player_name"].isin(ambiguous)]
    fitted = fitted[fitted["player_id"].isin(latest.index)]
    if fitted.empty:
        return pd.DataFrame(columns=columns)

    present = [c for c in BANK_STAT_TO_FP if c in fitted.columns]
    means = fitted.groupby("player_id")[present].mean()

    long = means.rename(columns=BANK_STAT_TO_FP).stack().reset_index()
    long.columns = ["player_id", "stat", "value"]
    long = long[long["value"] > 0.0]  # a stat he does not accumulate is not a mean
    for column in ("player_name", "team", "position"):
        long[column] = long["player_id"].map(latest[column]).fillna("").astype(str)
    return long[columns].reset_index(drop=True)
