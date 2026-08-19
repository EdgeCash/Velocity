"""Deep Dive card — the analytical companion to the matchup card, data side.

The matchup card is the tease: two numbers per market and a lean. This card is
the research page behind it, in the broadcast comparison-table genre (mirrored
team columns, league ranks, an ADV chip per row) — with one honest twist: the
stat rows are **the model's own inputs**. NFL rows are EPA/play by unit (the
exact quantities the rating fit consumes) plus scoring form; NCAAF rows are
scoring form plus the scores-fit ratings. So the table doesn't just compare
teams — it shows *why* the model's number sits where it does, and the margin
distribution beside it shows how far the market's line is from the model's
mass (cover/over probabilities included).

Everything here is computed from the committed datasets and the day's
projections — pure and offline-testable; rendering lives in
:mod:`deepdive_png`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from velocity.report.social import MarketView, SocialCard, WatchEntry

# A row's advantage fires only when the gap clears this fraction of the
# league's cross-team standard deviation for that stat — coin-flip edges stay
# unmarked, which is what keeps the marked ones credible.
ADV_STD_FRACTION = 0.35


@dataclass(frozen=True)
class StatRow:
    """One mirrored comparison row: away value | ranks | home value + the ADV."""

    label: str
    away_value: float
    home_value: float
    away_rank: int  # 1 = best in league for this stat
    home_rank: int
    n_teams: int
    advantage: str | None  # "away" / "home" / None (too close to call)
    fmt: str = "{:.1f}"

    def away_text(self) -> str:
        return self.fmt.format(self.away_value)

    def home_text(self) -> str:
        return self.fmt.format(self.home_value)


@dataclass(frozen=True)
class DeepDive:
    """Everything the deep-dive renderer needs for one game."""

    card: SocialCard  # identity, market view, fair numbers, record line
    stat_season: int  # the season the form rows are computed from
    away_record: str  # "10-2" season-to-date in stat_season
    home_record: str
    rows: tuple[StatRow, ...]
    margin_pmf: Mapping[int, float]  # home − away, full support
    p_home_cover: float | None  # vs the market home spread (None: no line)
    p_over: float | None  # vs the market total
    watch: Sequence[WatchEntry] = field(default_factory=tuple)  # extended props
    # Reference-genre header extras (all public schedule/results facts):
    away_last5: tuple[str, ...] = ()  # newest last: ("W","L","W","W","L")
    home_last5: tuple[str, ...] = ()
    away_streak: str = ""  # "W3" / "L1" / ""
    home_streak: str = ""
    away_rest: int | None = None  # full days since the last game
    home_rest: int | None = None
    away_sp: str | None = None  # MLB probable line ("JOBE 8.2 IP · 4.4 K/9")
    home_sp: str | None = None


def _rank(series: pd.Series, value: float, *, higher_is_better: bool) -> int:
    """1-indexed league rank of ``value`` within ``series``."""
    if higher_is_better:
        return int((series > value).sum()) + 1
    return int((series < value).sum()) + 1


def _advantage(
    series: pd.Series, away: float, home: float, *, higher_is_better: bool
) -> str | None:
    spread = float(series.std())
    if not np.isfinite(spread) or spread <= 0:
        return None
    if abs(away - home) < ADV_STD_FRACTION * spread:
        return None
    better_away = away > home if higher_is_better else away < home
    return "away" if better_away else "home"


def _row(
    label: str,
    per_team: pd.Series,
    away: str,
    home: str,
    *,
    higher_is_better: bool,
    fmt: str = "{:.1f}",
) -> StatRow | None:
    if away not in per_team.index or home not in per_team.index:
        return None
    away_v, home_v = float(per_team[away]), float(per_team[home])
    return StatRow(
        label=label,
        away_value=away_v,
        home_value=home_v,
        away_rank=_rank(per_team, away_v, higher_is_better=higher_is_better),
        home_rank=_rank(per_team, home_v, higher_is_better=higher_is_better),
        n_teams=len(per_team),
        advantage=_advantage(per_team, away_v, home_v,
                             higher_is_better=higher_is_better),
        fmt=fmt,
    )


def scoring_form(games: pd.DataFrame) -> tuple[int, pd.DataFrame]:
    """Per-team scoring form from the newest completed season-to-date.

    Returns ``(season, frame)`` with one row per team: ``ppg``, ``papg``
    (points allowed), ``wins``, ``losses`` — computed from every game with a
    final in the newest season that has any.
    """
    played = games[games["home_score"].notna() & games["away_score"].notna()]
    if played.empty:
        return 0, pd.DataFrame(columns=["ppg", "papg", "wins", "losses"])
    season = int(played["season"].max())
    span = played[played["season"] == season]
    home = span[["home_team", "home_score", "away_score"]].rename(columns={
        "home_team": "team", "home_score": "pf", "away_score": "pa"})
    away = span[["away_team", "away_score", "home_score"]].rename(columns={
        "away_team": "team", "away_score": "pf", "home_score": "pa"})
    long = pd.concat([home, away], ignore_index=True)
    long["win"] = (long["pf"] > long["pa"]).astype(int)
    long["tie"] = (long["pf"] == long["pa"]).astype(int)
    form = long.groupby("team").agg(
        ppg=("pf", "mean"), papg=("pa", "mean"),
        wins=("win", "sum"), ties=("tie", "sum"), games=("pf", "size"),
    )
    form["losses"] = form["games"] - form["wins"] - form["ties"]
    return season, form


def epa_form(plays: pd.DataFrame, season: int) -> pd.DataFrame:
    """Per-team EPA/play splits for ``season``: the model's own inputs.

    Columns: ``off_epa``/``def_epa`` (all pass+run plays), ``pass_off``/
    ``rush_off``/``pass_def``/``rush_def``. Defensive values are EPA *allowed*
    — lower is better, exactly as the ridge fit sees them.
    """
    span = plays[(plays["season"] == season)
                 & (plays["play_type"].isin(["pass", "run"]))].copy()
    if span.empty:
        return pd.DataFrame(columns=["off_epa", "def_epa", "pass_off",
                                     "rush_off", "pass_def", "rush_def"])
    is_pass = span["play_type"] == "pass"
    out = pd.DataFrame({
        "off_epa": span.groupby("posteam")["epa"].mean(),
        "def_epa": span.groupby("defteam")["epa"].mean(),
        "pass_off": span[is_pass].groupby("posteam")["epa"].mean(),
        "rush_off": span[~is_pass].groupby("posteam")["epa"].mean(),
        "pass_def": span[is_pass].groupby("defteam")["epa"].mean(),
        "rush_def": span[~is_pass].groupby("defteam")["epa"].mean(),
    })
    return out.dropna(how="all")


def team_form(
    games: pd.DataFrame, team: str, season: int, kickoff: object = None, n: int = 5
) -> dict[str, object]:
    """Last-``n`` results, streak, and rest days for ``team`` in ``season``.

    Results are newest-LAST (the reference reads left→right toward today);
    rest is full days between the team's most recent completed game and this
    game's kickoff (None when either side is unknown).
    """
    played = games[
        (games["season"] == season)
        & games["home_score"].notna() & games["away_score"].notna()
        & ((games["home_team"] == team) | (games["away_team"] == team))
    ]
    order = "kickoff" if "kickoff" in played.columns else ["season", "week"]
    played = played.sort_values(order)
    results: list[str] = []
    for g in played.to_dict("records"):
        us = g["home_score"] if g["home_team"] == team else g["away_score"]
        them = g["away_score"] if g["home_team"] == team else g["home_score"]
        results.append("W" if us > them else ("L" if us < them else "T"))
    streak = ""
    if results:
        mark = results[-1]
        run = len(results) - len("".join(results).rstrip(mark))
        streak = f"{mark}{run}"
    rest = None
    if (kickoff is not None and not pd.isna(kickoff)  # type: ignore[call-overload]
            and not played.empty and "kickoff" in played.columns):
        last = pd.Timestamp(played["kickoff"].iloc[-1])
        if not pd.isna(last):
            rest = max(0, int((pd.Timestamp(kickoff) - last).days))  # type: ignore[arg-type]
    return {"last5": tuple(results[-n:]), "streak": streak, "rest": rest}


def _ip_text(outs: float) -> str:
    """Total outs → the baseball innings convention ("134.2" = 134⅔)."""
    return f"{int(outs) // 3}.{int(outs) % 3}"


def probable_line(starters: pd.DataFrame | None, sp_id: str | None) -> str | None:
    """A probable's compact banked line: "SKENES · 134.2 IP · 9.6 K/9".

    Aggregated from the committed starters bank (defense-independent stats
    only — no ERA is banked, and K rate is the stabler skill signal anyway).
    None when the pitcher is unknown or has no banked innings.
    """
    if starters is None or sp_id is None:
        return None
    mine = starters[starters["starter_id"].astype(str) == str(sp_id)]
    mine = mine[pd.to_numeric(mine["outs"], errors="coerce") > 0]
    if mine.empty:
        return None
    outs = float(pd.to_numeric(mine["outs"], errors="coerce").sum())
    k9 = float(pd.to_numeric(mine["k"], errors="coerce").sum()) / (outs / 27.0)
    name = str(mine["starter_name"].iloc[-1]).split()[-1].upper()
    return f"{name} · {_ip_text(outs)} IP · {k9:.1f} K/9"


def _record(form: pd.DataFrame, team: str) -> str:
    if team not in form.index:
        return ""
    row = form.loc[team].to_dict()
    base = f"{int(row['wins'])}-{int(row['losses'])}"
    ties = int(row.get("ties", 0) or 0)
    if ties:
        base += f"-{ties}"
    return base


def build_rows(
    away: str,
    home: str,
    scoring: pd.DataFrame,
    epa: pd.DataFrame | None,
) -> tuple[StatRow, ...]:
    """The mirrored table: scoring form always; EPA unit rows when plays exist."""
    candidates: list[StatRow | None] = [
        _row("POINTS / GM", scoring["ppg"], away, home, higher_is_better=True),
        _row("POINTS ALLOWED / GM", scoring["papg"], away, home,
             higher_is_better=False),
    ]
    if epa is not None and not epa.empty:
        fmt = "{:+.3f}"
        candidates.extend([
            _row("EPA / PLAY — OFFENSE", epa["off_epa"], away, home,
                 higher_is_better=True, fmt=fmt),
            _row("EPA / PLAY — DEFENSE", epa["def_epa"], away, home,
                 higher_is_better=False, fmt=fmt),
            _row("PASS EPA / PLAY — OFF", epa["pass_off"], away, home,
                 higher_is_better=True, fmt=fmt),
            _row("RUSH EPA / PLAY — OFF", epa["rush_off"], away, home,
                 higher_is_better=True, fmt=fmt),
            _row("PASS EPA / PLAY — DEF", epa["pass_def"], away, home,
                 higher_is_better=False, fmt=fmt),
            _row("RUSH EPA / PLAY — DEF", epa["rush_def"], away, home,
                 higher_is_better=False, fmt=fmt),
        ])
    return tuple(row for row in candidates if row is not None)


def build_deep_dives(
    cards: Sequence[SocialCard],
    projections: Mapping[str, object],
    games: pd.DataFrame,
    plays: pd.DataFrame | None = None,
    *,
    team_names: Mapping[str, str] | None = None,
    starters: pd.DataFrame | None = None,
    probables: Mapping[tuple[str, str, None], tuple[str | None, str | None]] | None = None,
) -> list[DeepDive]:
    """One :class:`DeepDive` per social card, from the committed data.

    ``cards`` supply identity + market; ``projections`` the sims (margin pmf,
    cover/over probabilities); ``games``/``plays`` the form rows.
    ``team_names`` maps a card's display code back to the datasets' team key
    when they differ (NFL codes match; NCAAF cards may carry abbreviations
    while the datasets key by school name). MLB passes ``starters`` (the
    committed bank) plus the day's ``probables`` lookup (keyed by the
    datasets' full team names) to put each probable's banked line on the
    card header.
    """
    stat_season, scoring = scoring_form(games)
    epa = epa_form(plays, stat_season) if plays is not None else None
    names = dict(team_names or {})
    if starters is not None and "game_id" in games.columns:
        # The probable's line is his CURRENT season (the reference
        # convention), not the whole multi-season bank.
        season_ids = set(
            games.loc[games["season"] == stat_season, "game_id"].astype(str)
        )
        starters = starters[starters["game_id"].astype(str).isin(season_ids)]

    dives: list[DeepDive] = []
    for card in cards:
        proj = projections.get(card.game_id)
        if proj is None:
            continue
        away = names.get(card.away_code, card.away_code)
        home = names.get(card.home_code, card.home_code)
        margin = proj.sim.margin.astype(int)  # type: ignore[attr-defined]
        total = proj.sim.total  # type: ignore[attr-defined]
        values, counts = np.unique(margin, return_counts=True)
        margin_pmf = {int(v): float(c) / len(margin)
                      for v, c in zip(values, counts, strict=True)}
        view = card.market_view or MarketView()
        p_cover = None
        if view.spread_home is not None:
            p_cover = float(np.mean(margin > -view.spread_home))
        p_over = None
        if view.total is not None:
            p_over = float(np.mean(total > view.total))
        away_form = team_form(games, away, stat_season, card.kickoff)
        home_form = team_form(games, home, stat_season, card.kickoff)
        away_sp_id, home_sp_id = (None, None)
        if probables is not None:
            home_sp_id, away_sp_id = probables.get((home, away, None), (None, None))
        dives.append(DeepDive(
            card=card,
            stat_season=stat_season,
            away_record=_record(scoring, away),
            home_record=_record(scoring, home),
            rows=build_rows(away, home, scoring, epa),
            margin_pmf=margin_pmf,
            p_home_cover=p_cover,
            p_over=p_over,
            watch=card.watch,
            away_last5=away_form["last5"],  # type: ignore[arg-type]
            home_last5=home_form["last5"],  # type: ignore[arg-type]
            away_streak=str(away_form["streak"]),
            home_streak=str(home_form["streak"]),
            away_rest=away_form["rest"],  # type: ignore[arg-type]
            home_rest=home_form["rest"],  # type: ignore[arg-type]
            away_sp=probable_line(starters, away_sp_id),
            home_sp=probable_line(starters, home_sp_id),
        ))
    return dives


def deep_dive_caption(dive: DeepDive) -> str:
    """Post copy: the analytical summary as plain statements."""
    card = dive.card
    lines = [f"{card.away_code} @ {card.home_code} — the deep dive."]
    marked = [r for r in dive.rows if r.advantage]
    if marked:
        edges = ", ".join(
            f"{r.label.title()}: {card.away_code if r.advantage == 'away' else card.home_code}"
            for r in marked[:4]
        )
        lines.append(f"Unit edges ({dive.stat_season}): {edges}.")
    view = card.market_view
    if view is not None and view.spread_home is not None and dive.p_home_cover is not None:
        lines.append(
            f"Market {card.home_code} {view.spread_home:+g} covers in "
            f"{dive.p_home_cover:.0%} of {card.n_sims:,} sims "
            f"(model fair line {card.spread_label()})."
        )
    if view is not None and view.total is not None and dive.p_over is not None:
        lines.append(
            f"Over {view.total:g} hits in {dive.p_over:.0%} of sims "
            f"(model fair total {card.fair_total:.1f})."
        )
    return "\n".join(lines)
