"""Game context assembly — the raw material the intelligence layer reasons over.

The wagering engine prices bets off the simulation alone; this module gathers
the *surrounding evidence* for each game so qualifying bets can be judged
against it: season-to-date unit stats (EPA/play splits where plays exist,
scoring form always), recent performance (last-``n`` scoring, streak), rest
days, and the injury report's genuine outs.

Everything is computed from the committed datasets plus an optional injuries
snapshot (the ``collect_fantasypros`` artifact) — pure, offline-testable, and
point-in-time safe: pass ``as_of`` and only games completed before that moment
(and their plays) enter any number, so the layer can never see the future in a
backtest.

The stat definitions deliberately reuse :mod:`velocity.report.deepdive`'s
(``scoring_form`` / ``epa_form`` / ``team_form``) so the intelligence layer
judges bets on exactly the numbers the deep-dive card shows a human.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pandas as pd

from velocity.report.deepdive import epa_form, scoring_form, team_form

# Stat columns carried per team and z-scored against the league's cross-team
# spread. EPA defensive values are EPA *allowed* — higher means leakier.
EPA_COLS = ("off_epa", "def_epa", "pass_off", "rush_off", "pass_def", "rush_def")
SCORE_COLS = ("ppg", "papg")


@dataclass(frozen=True)
class InjuryOut:
    """One genuinely unavailable player (Out/IR/doubtful — not questionable)."""

    player_name: str
    position: str
    status: str


@dataclass(frozen=True)
class TeamContext:
    """One team's evidence for a game: units, form, rest, and outs."""

    team: str
    # Season-to-date EPA/play by unit (None when no plays data covers the team).
    off_epa: float | None = None
    def_epa: float | None = None
    pass_off: float | None = None
    rush_off: float | None = None
    pass_def: float | None = None
    rush_def: float | None = None
    # Season-to-date scoring form.
    ppg: float | None = None
    papg: float | None = None
    # Recent performance: means over the team's last ``form_games`` games.
    recent_ppg: float | None = None
    recent_papg: float | None = None
    last5: tuple[str, ...] = ()
    streak: str = ""
    rest_days: int | None = None
    outs: tuple[InjuryOut, ...] = ()

    def net_ppg(self) -> float | None:
        """Season scoring margin per game (None without both sides)."""
        if self.ppg is None or self.papg is None:
            return None
        return self.ppg - self.papg

    def recent_net_ppg(self) -> float | None:
        """Recent-window scoring margin per game."""
        if self.recent_ppg is None or self.recent_papg is None:
            return None
        return self.recent_ppg - self.recent_papg


@dataclass(frozen=True)
class GameContext:
    """Everything the signals need to judge one game's bets."""

    game_id: str
    season: int
    away: TeamContext
    home: TeamContext
    kickoff: pd.Timestamp | None = None
    # League cross-team mean and spread per stat, for z-scoring a matchup.
    baseline: Mapping[str, float] = field(default_factory=dict)
    dispersion: Mapping[str, float] = field(default_factory=dict)

    def picked(self, side: str) -> tuple[TeamContext, TeamContext]:
        """``(picked team, opponent)`` for a ``home``/``away`` bet side."""
        if side == "home":
            return self.home, self.away
        if side == "away":
            return self.away, self.home
        raise ValueError(f"side {side!r} does not pick a team")

    def zscore(self, stat: str, value: float) -> float | None:
        """``value`` in league standard deviations for ``stat`` (None: no spread)."""
        spread = self.dispersion.get(stat)
        mean = self.baseline.get(stat)
        if spread is None or mean is None or spread <= 0:
            return None
        return (value - mean) / spread


def _long_scores(span: pd.DataFrame) -> pd.DataFrame:
    """Completed games → one row per (team, game): ``team/pf/pa`` in play order."""
    order_cols = ["kickoff"] if "kickoff" in span.columns else ["season", "week"]
    span = span.sort_values(order_cols)
    home = span[["home_team", "home_score", "away_score"]].rename(
        columns={"home_team": "team", "home_score": "pf", "away_score": "pa"}
    )
    away = span[["away_team", "away_score", "home_score"]].rename(
        columns={"away_team": "team", "away_score": "pf", "home_score": "pa"}
    )
    home = home.assign(_order=range(len(span)))
    away = away.assign(_order=range(len(span)))
    return pd.concat([home, away], ignore_index=True).sort_values("_order")


def recent_scoring(span: pd.DataFrame, n: int) -> pd.DataFrame:
    """Per-team scoring means over each team's last ``n`` completed games.

    Returns a frame indexed by team with ``recent_ppg`` / ``recent_papg``.
    """
    if span.empty:
        return pd.DataFrame(columns=["recent_ppg", "recent_papg"])
    long = _long_scores(span)
    tail = long.groupby("team").tail(n)
    return tail.groupby("team").agg(recent_ppg=("pf", "mean"), recent_papg=("pa", "mean"))


def injuries_for_week(
    injuries: pd.DataFrame | None, season: int, week: int
) -> pd.DataFrame | None:
    """The injuries snapshot for one (season, week) from a history frame.

    The live path hands :class:`ContextLibrary` a current-report snapshot; a
    backtest hands it the banked history (``datasets/nfl/injuries.parquet``)
    sliced to the week being priced — this is that slice. A frame without
    ``season``/``week`` columns is already a snapshot and passes through
    unchanged; ``None`` stays ``None`` (signals abstain).
    """
    if injuries is None or injuries.empty:
        return injuries
    if "season" not in injuries.columns or "week" not in injuries.columns:
        return injuries
    mask = (injuries["season"] == season) & (injuries["week"] == week)
    return injuries[mask].reset_index(drop=True)


def _stat_spread(
    frame: pd.DataFrame, cols: tuple[str, ...]
) -> tuple[dict[str, float], dict[str, float]]:
    """Cross-team ``(mean, std)`` per column, skipping absent/degenerate ones."""
    means: dict[str, float] = {}
    stds: dict[str, float] = {}
    for col in cols:
        if col not in frame.columns:
            continue
        series = frame[col].dropna()
        if len(series) < 2:
            continue
        std = float(series.std())
        if std > 0:
            means[col] = float(series.mean())
            stds[col] = std
    return means, stds


def _value(frame: pd.DataFrame | None, team: str, col: str) -> float | None:
    if frame is None or col not in getattr(frame, "columns", ()) or team not in frame.index:
        return None
    series = frame[col]
    value = series[team]
    return None if pd.isna(value) else float(value)


@dataclass(frozen=True)
class ContextLibrary:
    """The per-slate context store: build once, hand out a ``GameContext`` per game."""

    season: int
    games: pd.DataFrame  # completed games entering any number (post ``as_of`` cut)
    scoring: pd.DataFrame  # per-team season scoring form
    epa: pd.DataFrame | None  # per-team EPA unit splits (None: no plays data)
    recent: pd.DataFrame  # per-team recent-window scoring
    injuries: pd.DataFrame | None  # normalized injuries snapshot (None: not supplied)
    baseline: Mapping[str, float]
    dispersion: Mapping[str, float]
    form_games: int

    @classmethod
    def build(
        cls,
        games: pd.DataFrame,
        plays: pd.DataFrame | None = None,
        injuries: pd.DataFrame | None = None,
        *,
        as_of: pd.Timestamp | None = None,
        form_games: int = 5,
    ) -> ContextLibrary:
        """Assemble the library from the committed datasets.

        ``as_of`` enforces point-in-time correctness: only games completed
        (final scores) with kickoff strictly before it count, and plays are
        restricted to those games. Live slates pass "now"; backtests pass the
        historical moment being priced.
        """
        completed = games[games["home_score"].notna() & games["away_score"].notna()]
        if as_of is not None and "kickoff" in completed.columns:
            kicked = pd.to_datetime(completed["kickoff"], errors="coerce")
            completed = completed[kicked < pd.Timestamp(as_of)]
        season, scoring = scoring_form(completed)
        span = completed[completed["season"] == season] if season else completed.iloc[0:0]

        epa: pd.DataFrame | None = None
        if plays is not None and season:
            scoped = plays
            if as_of is not None and "game_id" in plays.columns and "game_id" in completed.columns:
                allowed = set(completed["game_id"].astype(str))
                scoped = plays[plays["game_id"].astype(str).isin(allowed)]
            frame = epa_form(scoped, season)
            epa = frame if not frame.empty else None

        outs: pd.DataFrame | None = None
        if injuries is not None and not injuries.empty and "is_out" in injuries.columns:
            outs = injuries[injuries["is_out"].astype(bool)].copy()

        baseline: dict[str, float] = {}
        dispersion: dict[str, float] = {}
        score_mean, score_std = _stat_spread(scoring, SCORE_COLS)
        baseline.update(score_mean)
        dispersion.update(score_std)
        if not scoring.empty and {"ppg", "papg"} <= set(scoring.columns):
            net = (scoring["ppg"] - scoring["papg"]).dropna()
            if len(net) >= 2 and float(net.std()) > 0:
                baseline["net_ppg"] = float(net.mean())
                dispersion["net_ppg"] = float(net.std())
        if epa is not None:
            epa_mean, epa_std = _stat_spread(epa, EPA_COLS)
            baseline.update(epa_mean)
            dispersion.update(epa_std)

        return cls(
            season=season,
            games=completed,
            scoring=scoring,
            epa=epa,
            recent=recent_scoring(span, form_games),
            injuries=outs,
            baseline=baseline,
            dispersion=dispersion,
            form_games=form_games,
        )

    def outs_for(self, team: str) -> tuple[InjuryOut, ...]:
        """Genuine outs on ``team``'s report, empty when no snapshot was supplied."""
        if self.injuries is None or "team" not in self.injuries.columns:
            return ()
        mine = self.injuries[self.injuries["team"].astype(str) == str(team)]
        return tuple(
            InjuryOut(
                player_name=str(row.get("player_name") or ""),
                position=str(row.get("position") or ""),
                status=str(row.get("status") or ""),
            )
            for row in mine.to_dict("records")
        )

    def team_context(self, team: str, kickoff: pd.Timestamp | None = None) -> TeamContext:
        """One team's full evidence block, as of the library's data."""
        form = team_form(self.games, team, self.season, kickoff, n=5)
        return TeamContext(
            team=team,
            off_epa=_value(self.epa, team, "off_epa"),
            def_epa=_value(self.epa, team, "def_epa"),
            pass_off=_value(self.epa, team, "pass_off"),
            rush_off=_value(self.epa, team, "rush_off"),
            pass_def=_value(self.epa, team, "pass_def"),
            rush_def=_value(self.epa, team, "rush_def"),
            ppg=_value(self.scoring, team, "ppg"),
            papg=_value(self.scoring, team, "papg"),
            recent_ppg=_value(self.recent, team, "recent_ppg"),
            recent_papg=_value(self.recent, team, "recent_papg"),
            last5=form["last5"],  # type: ignore[arg-type]
            streak=str(form["streak"]),
            rest_days=form["rest"],  # type: ignore[arg-type]
            outs=self.outs_for(team),
        )

    def context_for(
        self,
        game_id: str,
        away: str,
        home: str,
        kickoff: pd.Timestamp | None = None,
    ) -> GameContext:
        """The :class:`GameContext` for one matchup, keyed by the model's team names."""
        return GameContext(
            game_id=str(game_id),
            season=self.season,
            away=self.team_context(away, kickoff),
            home=self.team_context(home, kickoff),
            kickoff=kickoff,
            baseline=self.baseline,
            dispersion=self.dispersion,
        )
