"""Sim Check — last night's actual result placed on the model's pregame distribution.

The argument-settling half of the card system. The pregame model card is a
ritual; this is the accountability post: "Final 27–20 — a 61st-percentile
total against the pregame distribution (fair total 47.5)." When the sims
called it, the card is the receipt; when the game landed in the tail, the card
says so just as plainly. No incumbent grades its own forecast visually — that
asymmetry is the point.

Percentiles use the mid-distribution convention — ``P(X < x) + ½·P(X = x)`` —
so a result sitting exactly on the modal value reads near the 50th, not the
0th or 100th. All inputs are the persisted pregame parquets plus the schedule
feed's finals; everything here is a pure function of frames.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


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
    ``finals`` carries ``home_score``/``away_score``; ``games_map`` names the
    teams. A game missing any piece is skipped — a Sim Check never guesses.
    An overtime tie can't happen in the graded feeds we read, but a zero
    margin is skipped defensively (no winner to grade).
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
        if margin == 0:
            continue

        p_home = float(row["p_home_win"])
        home_won = margin > 0
        home_margin_pct = mid_percentile(margin_pmf, margin)
        winner_pct = home_margin_pct if home_won else 1.0 - home_margin_pct

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
                total_pmf=total_pmf,
                n_sims=int(row.get("n_sims", 0) or 0),
            )
        )
    return cards


# The accuracy chain: one row per graded game — the Sim Check card without
# its pmf, plus the projection's own means. A card is a post; a frame is a
# record, and the site's Accuracy view (docs/FOOTBALL_PAL.md) is built from
# the season of these rather than from the cards.
SIM_CHECK_COLUMNS = [
    "game_id", "league", "game_date", "away_name", "home_name", "away_code",
    "home_code", "mu_away", "mu_home", "fair_spread", "fair_total", "p_home_win",
    "away_score", "home_score", "actual_total", "total_percentile",
    "winner_code", "winner_percentile", "p_winner_pregame", "n_sims",
    "graded_stamp",
]


def _num(value: object) -> float:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
    return out


def sim_check_frame(
    checks: list[SimCheckCard],
    projections: pd.DataFrame,
    league: str,
    graded_stamp: str,
) -> pd.DataFrame:
    """The day's Sim Checks as rows, with the projection's means beside the final.

    ``projections`` is the slate run's persisted frame (``mu_home``/``mu_away``/
    ``fair_spread``/``p_home_win`` by ``game_id``); a card whose projection row
    is missing keeps its own numbers and NaN for the rest.
    """
    if not checks:
        return pd.DataFrame(columns=SIM_CHECK_COLUMNS)
    by_game = {str(r["game_id"]): r for r in projections.to_dict("records")}
    rows = []
    for card in checks:
        proj = by_game.get(str(card.game_id), {})
        rows.append({
            "game_id": str(card.game_id),
            "league": league,
            "game_date": pd.NaT if card.game_date is None else pd.Timestamp(card.game_date),
            "away_name": card.away_name,
            "home_name": card.home_name,
            "away_code": card.away_code,
            "home_code": card.home_code,
            "mu_away": _num(proj.get("mu_away")),
            "mu_home": _num(proj.get("mu_home")),
            "fair_spread": _num(proj.get("fair_spread")),
            "fair_total": float(card.fair_total),
            "p_home_win": _num(proj.get("p_home_win")),
            "away_score": float(card.away_score),
            "home_score": float(card.home_score),
            "actual_total": float(card.actual_total),
            "total_percentile": float(card.total_percentile),
            "winner_code": card.winner_code,
            "winner_percentile": float(card.winner_percentile),
            "p_winner_pregame": float(card.p_winner_pregame),
            "n_sims": int(card.n_sims),
            "graded_stamp": graded_stamp,
        })
    return pd.DataFrame(rows, columns=SIM_CHECK_COLUMNS)


def merge_sim_check_chain(*chains: pd.DataFrame | None) -> pd.DataFrame:
    """The season's accuracy chain: every graded game once, the newest grade winning.

    Copies come from every previous run's artifact (each run carries the whole
    chain forward), so the union of every copy on hand is the chain, exactly
    as the record chain and the ledger merge — append-only rows keyed by
    identity, so no copy can overwrite another.
    """
    frames = [f for f in chains if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=SIM_CHECK_COLUMNS)
    merged = pd.concat(frames, ignore_index=True)
    for column in SIM_CHECK_COLUMNS:
        if column not in merged.columns:
            merged[column] = pd.NA
    merged = (
        merged.sort_values("graded_stamp", kind="stable")
        .drop_duplicates("game_id", keep="last")
        .sort_values(["game_date", "game_id"], kind="stable", na_position="last")
        .reset_index(drop=True)
    )
    return merged[SIM_CHECK_COLUMNS]


def sim_check_caption(card: SimCheckCard) -> str:
    """Post copy: the result, its percentile, and what the model said pregame."""
    return "\n".join([
        f"Final: {card.away_code} {card.away_score} — "
        f"{card.home_code} {card.home_score}.",
        f"The {card.actual_total} combined points were a "
        f"{ordinal(card.total_percentile)}-percentile outcome against the pregame "
        f"distribution (fair total {card.fair_total:.1f}).",
        f"{card.winner_code}'s margin was a {ordinal(card.winner_percentile)}-percentile "
        f"result; the model had {card.winner_code} at {card.p_winner_pregame:.0%} pregame.",
    ])
