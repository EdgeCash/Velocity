"""Yesterday's record — grade the previous slate into the email's model status.

The email's accountability section: what did yesterday's recommendations
actually do? Game plays grade through the segment-aware
:func:`~velocity.report.scorecard.grade_slate` (F5/NRFI bets settle against
linescore segment scores, never the full-game final), props grade against
per-player box scores, and parlays settle leg by leg from their structured
``legs_json`` — a pushed leg drops out, any lost leg loses the ticket, exactly
as a book grades it.

Everything here is a pure function of frames, so the whole record is
offline-testable; the network fetches (schedule, linescores, box scores) live in
``scripts/grade_yesterday.py``. A play that cannot be graded honestly (missing
final, shortened segment, player who never appeared) stays ``pending`` — the
record never guesses.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

from velocity.wagering.bet_log import Bet
from velocity.wagering.odds import net_payout

RECORD_COLUMNS = [
    "section", "play", "market", "side", "point", "price", "stake", "result", "profit",
]

_SEGMENT_FINAL_COLUMNS = {
    "_f5": ("home_score_f5", "away_score_f5"),
    "_i1": ("home_score_i1", "away_score_i1"),
}


def empty_record() -> pd.DataFrame:
    return pd.DataFrame(columns=RECORD_COLUMNS)


def _num(value: object) -> float | None:
    """A finite float, or ``None`` for missing/NaN/non-numeric — never a guess."""
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def _leg_result(
    leg: Mapping[Any, Any],
    finals_by_game: Mapping[str, Mapping[Any, Any]],
    box_by_game_player: Mapping[tuple[str, str], Mapping[Any, Any]],
) -> str | None:
    """One parlay leg's ``win``/``loss``/``push``, or ``None`` if ungradable."""
    game_id = str(leg["game_id"])
    point = _num(leg.get("point"))
    player = leg.get("player")
    if player is not None:
        stats = box_by_game_player.get((game_id, str(player)))
        actual = None if stats is None else _num(stats.get(str(leg["market"])))
        if actual is None or point is None:
            return None
        if actual == point:
            return "push"
        return "win" if (actual > point) == (leg["side"] == "over") else "loss"

    final = finals_by_game.get(game_id)
    if final is None:
        return None
    market = str(leg["market"])
    suffix = next((s for s in _SEGMENT_FINAL_COLUMNS if market.endswith(s)), None)
    home_col, away_col = (
        _SEGMENT_FINAL_COLUMNS[suffix] if suffix is not None else ("home_score", "away_score")
    )
    home, away = _num(final.get(home_col)), _num(final.get(away_col))
    price = _num(leg.get("price"))
    if home is None or away is None or price is None:
        return None
    bet = Bet(
        game_id=game_id, market=market, side=str(leg["side"]), book="",
        price=price, stake=1.0, p_model=0.5, point=point,
    )
    result, _ = bet.grade(home, away)
    return result


def grade_parlay_frame(
    parlays: pd.DataFrame,
    finals: pd.DataFrame,
    boxscores: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Settle each persisted parlay from its ``legs_json`` → ``result`` + ``profit``.

    Book convention: any lost leg loses the ticket; a pushed leg drops out (its
    decimal becomes 1); all legs pushing pushes the ticket. A ticket with any
    ungradable leg is ``pending`` — never partially settled.
    """
    finals_by_game: dict[str, Mapping[Any, Any]] = {
        str(r["game_id"]): r for r in finals.to_dict("records")
    }
    box_by_game_player: dict[tuple[str, str], Mapping[Any, Any]] = (
        {}
        if boxscores is None or boxscores.empty
        else {
            (str(r["game_id"]), str(r["player_id"])): r
            for r in boxscores.to_dict("records")
        }
    )
    results: list[str] = []
    profits: list[float] = []
    for row in parlays.to_dict("records"):
        legs = json.loads(str(row.get("legs_json") or "[]"))
        outcomes = [_leg_result(leg, finals_by_game, box_by_game_player) for leg in legs]
        stake = float(row.get("stake", 0.0) or 0.0)
        if not legs or any(o is None for o in outcomes):
            results.append("pending")
            profits.append(float("nan"))
        elif any(o == "loss" for o in outcomes):
            results.append("loss")
            profits.append(-stake)
        elif all(o == "push" for o in outcomes):
            results.append("push")
            profits.append(0.0)
        else:
            multiplier = 1.0
            for leg, outcome in zip(legs, outcomes, strict=True):
                if outcome == "win":
                    multiplier *= 1.0 + net_payout(float(leg["price"]))
            results.append("win")
            profits.append(stake * (multiplier - 1.0))
    graded = parlays.copy()
    graded["result"] = results
    graded["profit"] = profits
    return graded


def build_daily_record(
    games_graded: pd.DataFrame | None,
    props_graded: pd.DataFrame | None,
    parlays_graded: pd.DataFrame | None,
    *,
    matchups: Mapping[str, str] | None = None,
    slate_date: object = None,
) -> pd.DataFrame:
    """Fold the graded sections into one tidy record frame (``RECORD_COLUMNS``).

    ``play`` names what was bet: the matchup for game markets, the player for
    props, the legs string for parlays. ``slate_date`` (the graded slate's date)
    rides along on every row so the headline can date the record.
    """
    matchups = matchups or {}
    rows: list[dict[str, object]] = []

    def _base(row: Mapping[Any, Any], section: str, play: str) -> dict[str, object]:
        return {
            "section": section,
            "play": play,
            "market": str(row.get("market", "")),
            "side": str(row.get("side", "")),
            "point": row.get("point"),
            "price": row.get("price"),
            "stake": row.get("stake"),
            "result": row.get("result"),
            "profit": row.get("profit"),
        }

    if games_graded is not None:
        for row in games_graded.to_dict("records"):
            play = matchups.get(str(row.get("game_id")), str(row.get("game_id")))
            rows.append(_base(row, "games", play))
    if props_graded is not None:
        for row in props_graded.to_dict("records"):
            rows.append(_base(row, "props", str(row.get("player", ""))))
    if parlays_graded is not None:
        for row in parlays_graded.to_dict("records"):
            base = _base(row, "parlays", str(row.get("legs", "")))
            base["market"] = "parlay"
            base["side"] = ""
            base["point"] = None
            rows.append(base)

    record = pd.DataFrame(rows, columns=RECORD_COLUMNS) if rows else empty_record()
    record["slate_date"] = (
        pd.NaT if slate_date is None else pd.Timestamp(slate_date)  # type: ignore[arg-type]
    )
    return record


def accumulate_record(
    cumulative: pd.DataFrame | None, record: pd.DataFrame | None
) -> pd.DataFrame:
    """Fold a newly graded day into the season-to-date record.

    Dedup key is the play's identity (slate date + section + play + market +
    side + point); a regrade of the same play replaces the old row. This is the
    rolling ledger each run carries forward in its artifact, so the season
    record survives artifact retention as long as the chain of runs does.
    """
    frames = [f for f in (cumulative, record) if f is not None and not f.empty]
    if not frames:
        out = empty_record()
        out["slate_date"] = pd.NaT
        return out
    merged = pd.concat(frames, ignore_index=True)
    keys = ["slate_date", "section", "play", "market", "side", "point"]
    return merged.drop_duplicates(subset=keys, keep="last").reset_index(drop=True)


def record_summary(record: pd.DataFrame | None) -> dict[str, float]:
    """Settled record: wins, losses, pushes, and net units (pending excluded)."""
    if record is None or record.empty:
        return {"wins": 0, "losses": 0, "pushes": 0, "units": 0.0}
    return {
        "wins": int((record["result"] == "win").sum()),
        "losses": int((record["result"] == "loss").sum()),
        "pushes": int((record["result"] == "push").sum()),
        "units": float(record["profit"].dropna().sum()),
    }


def season_record_line(cumulative: pd.DataFrame | None) -> str | None:
    """"SEASON 41-38 · +6.2U" for the daily card footer; None with no record."""
    summary = record_summary(cumulative)
    if summary["wins"] + summary["losses"] + summary["pushes"] == 0:
        return None
    line = f"SEASON {summary['wins']}-{summary['losses']}"
    if summary["pushes"]:
        line += f"-{summary['pushes']}"
    return f"{line} · {summary['units']:+.1f}U"


def _section_line(record: pd.DataFrame, section: str) -> str | None:
    rows = record[record["section"] == section]
    if rows.empty:
        return None
    wins = int((rows["result"] == "win").sum())
    losses = int((rows["result"] == "loss").sum())
    pushes = int((rows["result"] == "push").sum())
    pending = int((rows["result"] == "pending").sum())
    profit = float(rows["profit"].dropna().sum())
    line = f"{section} {wins}-{losses}"
    if pushes:
        line += f"-{pushes}"
    line += f" ({profit:+.1f}u)"
    if pending:
        line += f" [{pending} pending]"
    return line


def record_headline(record: pd.DataFrame) -> str:
    """One line for the email: per-section records + total units, dated.

    Profits are in bankroll units of the graded slate's stake sizing.
    """
    when = record["slate_date"].dropna()
    date_tag = "" if when.empty else f" ({pd.Timestamp(when.iloc[0]).strftime('%b %-d')})"
    if record.empty:
        return f"Yesterday{date_tag}: no plays."
    parts = [
        line
        for section in ("games", "props", "parlays")
        if (line := _section_line(record, section)) is not None
    ]
    total = float(record["profit"].dropna().sum())
    return f"Yesterday{date_tag}: " + " · ".join(parts) + f" · total {total:+.1f}u"
