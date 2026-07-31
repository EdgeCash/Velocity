"""Sim Check — last night's actual result placed on the model's pregame distribution.

The argument-settling half of the card system. The pregame model card is a
ritual; this is the accountability post: "Final 11–4 — a 96th-percentile run
outcome against the pregame distribution (fair total 8.5)." When the sims
called it, the card is the receipt; when the game landed in the tail, the card
says so just as plainly. No incumbent grades its own forecast visually — that
asymmetry is the point.

Percentiles use the mid-distribution convention — ``P(X < x) + ½·P(X = x)`` —
so a result sitting exactly on the modal value reads near the 50th, not the
0th or 100th. All inputs are the persisted pregame parquets plus StatsAPI
finals; everything here is a pure function of frames.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

_KINDS = ("total", "margin")


def mid_percentile(pmf: dict[int, float], actual: float) -> float:
    """``P(X < actual) + ½·P(X = actual)`` over an integer pmf, in [0, 1]."""
    below = sum(p for v, p in pmf.items() if v < actual)
    at = sum(p for v, p in pmf.items() if v == actual)
    return below + 0.5 * at


def ordinal(value: float) -> str:
    """A percentile in [0, 1] → its ordinal label ("96th", "3rd", "50th")."""
    n = int(round(value * 100))
    n = min(max(n, 1), 99)  # 0th/100th overstate certainty a pmf can't carry
    teens = 10 <= n % 100 <= 20
    suffix = "th" if teens else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


@dataclass(frozen=True)
class SimCheckCard:
    """Everything the Sim Check renderer needs for one graded game."""

    game_id: str
    away_name: str
    home_name: str
    away_code: str
    home_code: str
    game_date: pd.Timestamp | None
    away_score: int
    home_score: int
    actual_total: int
    total_percentile: float  # of the actual total vs the pregame distribution
    winner_code: str
    winner_percentile: float  # of the winner's margin vs the pregame distribution
    p_winner_pregame: float  # the model's pregame probability on the actual winner
    fair_total: float
    p_yrfi: float | None
    yrfi_actual: bool | None  # None when the linescore didn't grade
    total_pmf: dict[int, float]
    n_sims: int


def _pmfs_by_game(distributions: pd.DataFrame) -> dict[tuple[str, str], dict[int, float]]:
    out: dict[tuple[str, str], dict[int, float]] = {}
    for row in distributions.to_dict("records"):
        key = (str(row["game_id"]), str(row["kind"]))
        out.setdefault(key, {})[int(row["value"])] = float(row["prob"])
    return out


def build_sim_checks(
    projections: pd.DataFrame,
    distributions: pd.DataFrame,
    finals: pd.DataFrame,
    games_map: pd.DataFrame,
) -> list[SimCheckCard]:
    """One card per game with a final score and both pregame pmfs.

    ``projections``/``distributions`` are the slate run's persisted parquets;
    ``finals`` carries ``home_score``/``away_score`` (and, when the linescore
    graded, ``home_score_i1``/``away_score_i1``); ``games_map`` names the teams.
    A game missing any piece is skipped — a Sim Check never guesses.
    """
    pmfs = _pmfs_by_game(distributions)
    finals_by_game = {str(r["game_id"]): r for r in finals.to_dict("records")}
    names_by_game = {str(r["game_id"]): r for r in games_map.to_dict("records")}

    cards: list[SimCheckCard] = []
    for row in projections.to_dict("records"):
        gid = str(row["game_id"])
        final = finals_by_game.get(gid)
        game = names_by_game.get(gid)
        total_pmf = pmfs.get((gid, "total"))
        margin_pmf = pmfs.get((gid, "margin"))
        if final is None or game is None or not total_pmf or not margin_pmf:
            continue
        home = final.get("home_score")
        away = final.get("away_score")
        if home is None or away is None or pd.isna(home) or pd.isna(away):
            continue
        home_i, away_i = int(home), int(away)
        margin = home_i - away_i
        if margin == 0:  # a suspended/called tie has no winner to grade
            continue

        p_home = float(row["p_home_win"])
        home_won = margin > 0
        home_margin_pct = mid_percentile(margin_pmf, margin)
        winner_pct = home_margin_pct if home_won else 1.0 - home_margin_pct

        i1_home = final.get("home_score_i1")
        i1_away = final.get("away_score_i1")
        yrfi_actual = (
            None
            if i1_home is None or i1_away is None or pd.isna(i1_home) or pd.isna(i1_away)
            else bool(float(i1_home) + float(i1_away) > 0)
        )
        p_yrfi = row.get("p_yrfi")
        kickoff = game.get("kickoff")
        cards.append(
            SimCheckCard(
                game_id=gid,
                away_name=str(game["away_team"]),
                home_name=str(game["home_team"]),
                away_code=str(row.get("away", "")),
                home_code=str(row.get("home", "")),
                game_date=None if pd.isna(kickoff) else pd.Timestamp(kickoff),
                away_score=away_i,
                home_score=home_i,
                actual_total=home_i + away_i,
                total_percentile=mid_percentile(total_pmf, home_i + away_i),
                winner_code=str(row.get("home" if home_won else "away", "")),
                winner_percentile=winner_pct,
                p_winner_pregame=p_home if home_won else 1.0 - p_home,
                fair_total=float(row["fair_total"]),
                p_yrfi=None if p_yrfi is None or pd.isna(p_yrfi) else float(p_yrfi),
                yrfi_actual=yrfi_actual,
                total_pmf=total_pmf,
                n_sims=int(row.get("n_sims", 0) or 0),
            )
        )
    return cards


def sim_check_caption(card: SimCheckCard) -> str:
    """Post copy: the result, its percentile, and what the model said pregame."""
    lines = [
        f"Final: {card.away_code} {card.away_score} — "
        f"{card.home_code} {card.home_score}.",
        f"The {card.actual_total} combined runs were a "
        f"{ordinal(card.total_percentile)}-percentile outcome against the pregame "
        f"distribution (fair total {card.fair_total:.1f}).",
        f"{card.winner_code}'s margin was a {ordinal(card.winner_percentile)}-percentile "
        f"result; the model had {card.winner_code} at {card.p_winner_pregame:.0%} pregame.",
    ]
    if card.p_yrfi is not None and card.yrfi_actual is not None:
        happened = "scored" if card.yrfi_actual else "didn't score"
        lines.append(
            f"First-inning run: model {card.p_yrfi:.0%} pregame — teams {happened}."
        )
    return "\n".join(lines)
