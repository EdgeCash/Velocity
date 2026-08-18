"""Season outlook — one team's full-season projection, game by game.

The FPI-screenshot format (the single most re-shared analytics artifact in
football media): every game of a team's season in one column — finished games
as results, remaining games as the model's win probability — topped by the
projected record. Pure data assembly here; the PNG lives in
:mod:`velocity.report.outlook_png`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

__all__ = ["OutlookRow", "SeasonOutlook", "build_outlook"]

# project(home_team, away_team) → P(home wins). The runner's projector fits
# this shape for both leagues (kickoff/neutral niceties are the runner's
# concern, not the outlook's).
Projector = Callable[[str, str], float]


@dataclass(frozen=True)
class OutlookRow:
    """One game on the team's schedule, finished or ahead."""

    week: int
    opponent: str
    at_home: bool
    p_win: float | None  # the model's number — None once the game is played
    result: str | None  # "W"/"L"/"T" — None until played
    score: str | None  # "27-20" (team score first) — None until played

    @property
    def played(self) -> bool:
        return self.result is not None


@dataclass(frozen=True)
class SeasonOutlook:
    """The card's data: the season line plus every game row in week order."""

    team: str
    season: int
    rows: tuple[OutlookRow, ...]
    wins: int  # banked
    losses: int
    ties: int
    expected_wins: float  # banked + the model's sum over remaining games

    @property
    def n_games(self) -> int:
        return len(self.rows)

    @property
    def expected_losses(self) -> float:
        return self.n_games - self.expected_wins - 0.5 * self.ties

    def projected_line(self) -> str:
        """The hero string: "PROJECTED 10.4–6.6" (ties folded half each way)."""
        return f"PROJECTED {self.expected_wins:.1f}–{self.expected_losses:.1f}"


def build_outlook(
    schedule: pd.DataFrame,
    team: str,
    project: Projector,
    *,
    season: int | None = None,
) -> SeasonOutlook:
    """Assemble a team's season outlook from a schedule frame and a projector.

    ``schedule`` needs ``season, week, home_team, away_team, home_score,
    away_score`` (scores null for unplayed games). ``season`` defaults to the
    newest in the frame. Played games contribute their actual result; each
    remaining game contributes ``project``'s win probability. Expected wins =
    banked wins + half a win per tie + the probability sum.
    """
    year = int(schedule["season"].max()) if season is None else int(season)
    mine = schedule[
        (schedule["season"] == year)
        & ((schedule["home_team"] == team) | (schedule["away_team"] == team))
    ].sort_values("week")
    if mine.empty:
        raise ValueError(f"{team!r} has no {year} games in the schedule")

    rows: list[OutlookRow] = []
    wins = losses = ties = 0
    expected = 0.0
    for g in mine.to_dict("records"):
        at_home = str(g["home_team"]) == team
        opponent = str(g["away_team"] if at_home else g["home_team"])
        home_score, away_score = g.get("home_score"), g.get("away_score")
        if pd.notna(home_score) and pd.notna(away_score):
            ours = float(home_score if at_home else away_score)
            theirs = float(away_score if at_home else home_score)
            if ours > theirs:
                result = "W"
                wins += 1
                expected += 1.0
            elif ours < theirs:
                result = "L"
                losses += 1
            else:
                result = "T"
                ties += 1
                expected += 0.5
            rows.append(OutlookRow(
                week=int(g["week"]), opponent=opponent, at_home=at_home,
                p_win=None, result=result, score=f"{ours:g}-{theirs:g}",
            ))
            continue
        p_home = float(project(str(g["home_team"]), str(g["away_team"])))
        p_win = p_home if at_home else 1.0 - p_home
        expected += p_win
        rows.append(OutlookRow(
            week=int(g["week"]), opponent=opponent, at_home=at_home,
            p_win=p_win, result=None, score=None,
        ))

    return SeasonOutlook(
        team=team, season=year, rows=tuple(rows),
        wins=wins, losses=losses, ties=ties, expected_wins=expected,
    )
