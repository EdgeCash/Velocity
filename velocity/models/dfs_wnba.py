"""WNBA DraftKings projections — a per-minute rate times the minutes she plays.

DK pays production, and production is a rate times opportunity. A WNBA roster
runs eight or nine deep, so the minute split is the largest single term in any
projection: a starter's 32 minutes against a reserve's 11 separates two players
with identical per-minute value by a factor of three. So the model is
deliberately two factors and no more:

    player DK  =  dk_per_minute(player)  x  E[minutes | her recent role]

Both are empirical-Bayes shrunk toward a **positional** prior — guards, wings
and posts bank DK points differently (assists at 1.5 against rebounds at 1.25
and blocks at 2.0) — so a player with four games prices near her position's
mean rather than off a four-game fluke. Everything is fitted from the banked
player boxes (``datasets/wnba/player_box.parquet``); nothing is imported from
a projection service, because none serves this league for free.

Minutes are read from her **recent** games rather than her season, because a
role change is the thing a season average is slowest to see. A player the bank
has never seen prices at her position's mean rather than being guessed.

Pure functions of frames; offline-testable, no network.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# DraftKings basketball scoring, which is the same line for the WNBA and the
# NBA. UNLIKE every other scoring table in this repo, these constants were
# hand-entered from DraftKings' published rules rather than read from an API:
# there is no machine-readable source for them. The roster template comes from
# DK's rules endpoint (game type 37), but that payload carries no scoring, the
# help page renders client-side, and every scoring-shaped path 404s
# (docs/DFS_FORMATS.md). :func:`scoring_disagreement` is the check that closes
# this the first time a live WNBA board quotes DK's own fantasy-points average.
DK_WNBA_POINTS = {
    "points": 1.0,
    "three_point_field_goals_made": 0.5,
    "rebounds": 1.25,
    "assists": 1.5,
    "steals": 2.0,
    "blocks": 2.0,
    "turnovers": -0.5,
}
# The stats a double-double is counted across, and what the two bonuses pay.
# DK stacks them: a triple-double banks the double-double bonus as well.
DOUBLE_CATEGORIES = ("points", "rebounds", "assists", "steals", "blocks")
DOUBLE_DOUBLE_BONUS = 1.5
TRIPLE_DOUBLE_BONUS = 3.0

# Empirical-Bayes prior strengths, each in its own unit, swept walk-forward
# over 11,668 player-games (docs/DFS_MODEL.md). Two findings, and both say the
# same thing: **do not smooth the minutes**.
#
# The sweep is monotone toward no minute prior at all and toward the shortest
# window tried — a rotation change is precisely what a longer window and a
# league-mean prior are slowest to see, and it is the largest single term in
# the projection. Its limit (prior 0, window 5) scores a within-slate rank
# correlation of 0.7161; the promoted point below scores 0.7157, a difference
# of four ten-thousandths on the metric a lineup is actually built on, and it
# keeps a one-game prior so a single appearance can never *fully* determine
# what a player is projected to play. The endpoint is a limit, not a bar.
RATE_PRIOR_MINUTES = 60.0
MINUTES_PRIOR_GAMES = 1.0
MINUTES_WINDOW_GAMES = 5
# A rate estimated off a handful of garbage-time minutes is noise; below this
# the player is priced at her position's mean outright.
MIN_MINUTES_FOR_RATE = 20.0
# Positions the release uses. Anything else buckets to the league mean.
POSITIONS = ("G", "F", "C")


def wnba_dk_points(frame: pd.DataFrame) -> pd.Series:
    """Realized DK points for each player-game row in a box-score frame.

    The counting line at DK's weights plus the double-double and triple-double
    bonuses. A missing stat column contributes zero rather than raising, so a
    thinner historical season still scores.
    """
    if frame.empty:
        return pd.Series(dtype=float)
    total = pd.Series(0.0, index=frame.index)
    for column, weight in DK_WNBA_POINTS.items():
        if column in frame.columns:
            total += weight * pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    doubles = pd.Series(0, index=frame.index)
    for column in DOUBLE_CATEGORIES:
        if column in frame.columns:
            doubles += (pd.to_numeric(frame[column], errors="coerce").fillna(0.0) >= 10).astype(int)
    total += DOUBLE_DOUBLE_BONUS * (doubles >= 2).astype(float)
    total += TRIPLE_DOUBLE_BONUS * (doubles >= 3).astype(float)
    return total


def scoring_disagreement(
    projected: pd.DataFrame, board: pd.DataFrame, *, name_column: str = "player_name"
) -> pd.DataFrame:
    """Our DK points per game against DraftKings' own, player by player.

    The one check that can confirm :data:`DK_WNBA_POINTS`, and it needs a live
    WNBA board: DK publishes a fantasy-points-per-game figure on the draftables
    payload, so once one exists this compares it against the same number
    computed from the box scores. A systematic gap is a wrong constant, not a
    bad projection — the two are computed from the same games.

    Returns ``[player_name, ours, draftkings, gap]`` for the players on both.
    """
    columns = ["player_name", "ours", "draftkings", "gap"]
    if projected.empty or board.empty or "fppg" not in board.columns:
        return pd.DataFrame(columns=columns)
    left = projected.rename(columns={name_column: "player_name"})[["player_name", "points"]]
    right = board.rename(columns={name_column: "player_name"})[["player_name", "fppg"]]
    keyed = left.assign(_k=left["player_name"].astype(str).str.lower().str.strip()).merge(
        right.assign(_k=right["player_name"].astype(str).str.lower().str.strip()),
        on="_k", how="inner", suffixes=("", "_dk"))
    if keyed.empty:
        return pd.DataFrame(columns=columns)
    out = pd.DataFrame({
        "player_name": keyed["player_name"],
        "ours": pd.to_numeric(keyed["points"], errors="coerce"),
        "draftkings": pd.to_numeric(keyed["fppg"], errors="coerce"),
    })
    out["gap"] = out["ours"] - out["draftkings"]
    return out.dropna(subset=["ours", "draftkings"]).reset_index(drop=True)


def _position(frame: pd.DataFrame) -> pd.Series:
    """The release's position letter, anything unknown bucketed as ``''``."""
    pos = frame.get("athlete_position_abbreviation")
    if pos is None:
        return pd.Series("", index=frame.index)
    cleaned = pos.astype(str).str.upper().str.strip().str[0]
    return cleaned.where(cleaned.isin(POSITIONS), "")


@dataclass(frozen=True)
class WnbaDfsModel:
    """Fitted per-minute DK rates and expected minutes, both shrunk.

    ``rate_of`` is DK points per minute by athlete id, ``minutes_of`` her
    expected minutes, and the two ``prior`` maps carry the positional means a
    player the bank barely knows is pulled toward.
    """

    rate_of: dict[str, float]
    minutes_of: dict[str, float]
    position_of: dict[str, str]
    rate_prior: dict[str, float]
    minutes_prior: dict[str, float]
    league_rate: float
    league_minutes: float

    @classmethod
    def fit(
        cls, player_box: pd.DataFrame, *, before: pd.Timestamp | None = None
    ) -> WnbaDfsModel:
        """Fit from banked player boxes, optionally only those before a date.

        ``before`` is what keeps a backtest honest: a projection may only see
        games that had already been played when it would have been made.
        """
        frame = player_box.copy()
        if frame.empty:
            return cls({}, {}, {}, {}, {}, 0.0, 0.0)
        frame["game_date"] = pd.to_datetime(frame["game_date"], errors="coerce")
        if before is not None:
            frame = frame[frame["game_date"] < pd.Timestamp(before)]
        frame = frame[pd.to_numeric(frame["minutes"], errors="coerce").fillna(0.0) > 0]
        if frame.empty:
            return cls({}, {}, {}, {}, {}, 0.0, 0.0)

        frame["_minutes"] = pd.to_numeric(frame["minutes"], errors="coerce").fillna(0.0)
        frame["_dk"] = wnba_dk_points(frame)
        frame["_pos"] = _position(frame)
        frame["athlete_id"] = frame["athlete_id"].astype(str)

        league_rate = float(frame["_dk"].sum() / frame["_minutes"].sum())
        league_minutes = float(frame["_minutes"].mean())

        by_pos = frame.groupby("_pos")
        rate_prior = {
            str(pos): float(rows["_dk"].sum() / rows["_minutes"].sum())
            for pos, rows in by_pos if rows["_minutes"].sum() > 0
        }
        minutes_prior = {str(pos): float(rows["_minutes"].mean()) for pos, rows in by_pos}

        # The season-long rate, shrunk toward the position by minutes played.
        totals = frame.groupby("athlete_id").agg(
            dk=("_dk", "sum"), minutes=("_minutes", "sum"), games=("_minutes", "size"))
        latest_rows = (frame.sort_values("game_date")
                       .drop_duplicates("athlete_id", keep="last"))
        position_of = {str(a): str(p) for a, p
                       in zip(latest_rows["athlete_id"], latest_rows["_pos"], strict=True)}

        rate_of: dict[str, float] = {}
        for athlete, row in totals.iterrows():
            key = str(athlete)
            prior = rate_prior.get(position_of.get(key, ""), league_rate)
            minutes = float(row["minutes"])
            if minutes < MIN_MINUTES_FOR_RATE:
                rate_of[key] = prior
                continue
            weight = minutes / (minutes + RATE_PRIOR_MINUTES)
            rate_of[key] = weight * (float(row["dk"]) / minutes) + (1 - weight) * prior

        # Minutes off her most recent games — a role change is what a season
        # average is slowest to see — shrunk toward the position by game count.
        recent = (frame.sort_values("game_date")
                  .groupby("athlete_id").tail(MINUTES_WINDOW_GAMES)
                  .groupby("athlete_id")["_minutes"].agg(["mean", "size"]))
        minutes_of: dict[str, float] = {}
        for athlete, row in recent.iterrows():
            key = str(athlete)
            prior = minutes_prior.get(position_of.get(key, ""), league_minutes)
            n = float(row["size"])
            weight = n / (n + MINUTES_PRIOR_GAMES)
            minutes_of[key] = weight * float(row["mean"]) + (1 - weight) * prior

        return cls(rate_of=rate_of, minutes_of=minutes_of, position_of=position_of,
                   rate_prior=rate_prior, minutes_prior=minutes_prior,
                   league_rate=league_rate, league_minutes=league_minutes)

    def project(self, athlete_id: str, position: str = "") -> float:
        """Expected DK points for one player — her rate times her minutes."""
        key = str(athlete_id)
        pos = self.position_of.get(key, str(position).upper().strip()[:1])
        rate = self.rate_of.get(key, self.rate_prior.get(pos, self.league_rate))
        minutes = self.minutes_of.get(key, self.minutes_prior.get(pos, self.league_minutes))
        return float(rate * minutes)

    def projections(self, player_box: pd.DataFrame) -> pd.DataFrame:
        """Every player the fit knows, in the optimizer's own shape.

        ``[player_id, player_name, team, position, points]`` — the same frame
        :func:`velocity.dfs.scoring.dk_expected_points` emits, so the pool join
        and the optimizer consume it unchanged.
        """
        columns = ["player_id", "player_name", "team", "position", "points"]
        if player_box.empty or not self.rate_of:
            return pd.DataFrame(columns=columns)
        latest = (player_box.assign(
            _d=pd.to_datetime(player_box["game_date"], errors="coerce"))
            .sort_values("_d").drop_duplicates("athlete_id", keep="last"))
        rows = [
            {
                "player_id": str(row["athlete_id"]),
                "player_name": str(row["athlete_display_name"]),
                "team": str(row.get("team_abbreviation") or ""),
                "position": str(row.get("athlete_position_abbreviation") or "").upper()[:1],
                "points": round(self.project(str(row["athlete_id"])), 2),
            }
            for row in latest.to_dict("records")
        ]
        return pd.DataFrame(rows, columns=columns)
