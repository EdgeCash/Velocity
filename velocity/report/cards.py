"""Assemble per-game matchup cards from projections + lines + context.

Turns the pieces the live runner already has — the model projections, the
canonical odds board, and the StatsAPI game context — into a list of plain card
dicts the renderer (:mod:`velocity.report.card_html`) draws. The one bit of new
logic is a per-market recommendation: for moneyline / total / run line, compare
the model's probability to the de-vigged market and grade the edge into a
confidence score and a PLAY / LEAN / PASS call (every market, not just the staked
bets the slate surfaces).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

from velocity.ingest.mlb_context import GameContext, PitcherContext
from velocity.wagering.devig import devig
from velocity.wagering.live import MLB_TEAM_ALIASES, resolve_team


def _call(conf: float) -> str:
    return "PLAY" if conf >= 8 else "LEAN" if conf >= 4 else "PASS"


def _grade(model_prob: float, price_side: float, price_opp: float) -> tuple[float, str]:
    """Confidence (0-9.9) + call from the edge of ``model_prob`` over the de-vig."""
    fair = devig([price_side, price_opp])[0]
    conf = round(min(max(model_prob - fair, 0.0) * 200.0, 9.9), 1)
    return conf, _call(conf)


def _median(df: pd.DataFrame, side: str) -> tuple[float | None, float | None]:
    rows = df[df["side"] == side]
    if rows.empty:
        return None, None
    point = None if rows["point"].isna().all() else float(rows["point"].median())
    return float(rows["price"].median()), point


def _rec(label: str, pick: str, conf: float, call: str) -> dict[str, Any]:
    return {"label": label, "pick": pick, "conf": conf, "call": call}


def _pass(label: str) -> dict[str, Any]:
    return _rec(label, "n/a", 0.0, "PASS")


def recommend_for_game(
    projection: Any, game_lines: pd.DataFrame, home_code: str, away_code: str
) -> list[dict[str, Any]]:
    """Moneyline / Run Line / Total recommendations for one game (best side each)."""
    recs: list[dict[str, Any]] = []

    ml = game_lines[game_lines["market"] == "moneyline"]
    hp, _ = _median(ml, "home")
    ap, _ = _median(ml, "away")
    if hp is not None and ap is not None:
        ph = float(projection.p_home_win())
        conf_h, call_h = _grade(ph, hp, ap)
        conf_a, call_a = _grade(1 - ph, ap, hp)
        if conf_h >= conf_a:
            recs.append(_rec("MONEYLINE", f"{home_code} {int(hp):+d}", conf_h, call_h))
        else:
            recs.append(_rec("MONEYLINE", f"{away_code} {int(ap):+d}", conf_a, call_a))
    else:
        recs.append(_pass("MONEYLINE"))

    sp = game_lines[game_lines["market"] == "spread"]
    hp, hpt = _median(sp, "home")
    ap, apt = _median(sp, "away")
    if hp is not None and ap is not None and hpt is not None:
        ph = float(projection.prob_home_cover(hpt))
        conf_h, call_h = _grade(ph, hp, ap)
        conf_a, call_a = _grade(1 - ph, ap, hp)
        if conf_h >= conf_a:
            recs.append(_rec("RUN LINE", f"{home_code} {hpt:+.1f}", conf_h, call_h))
        else:
            recs.append(_rec("RUN LINE", f"{away_code} {-hpt:+.1f}", conf_a, call_a))
    else:
        recs.append(_pass("RUN LINE"))

    tot = game_lines[game_lines["market"] == "total"]
    op, opt = _median(tot, "over")
    up, _ = _median(tot, "under")
    if op is not None and up is not None and opt is not None:
        over = float(projection.prob_over(opt))
        conf_o, call_o = _grade(over, op, up)
        conf_u, call_u = _grade(1 - over, up, op)
        if conf_o >= conf_u:
            recs.append(_rec("TOTAL", f"Over {opt:g}", conf_o, call_o))
        else:
            recs.append(_rec("TOTAL", f"Under {opt:g}", conf_u, call_u))
    else:
        recs.append(_pass("TOTAL"))

    return recs


def _pitcher_dict(sp: PitcherContext | None, fallback_name: str) -> dict[str, Any]:
    if sp is None:
        return {"id": None, "name": fallback_name, "hand": None, "line": None}
    return {"id": sp.player_id, "name": sp.name, "hand": sp.hand, "line": sp.line}


def context_index(
    contexts: Iterable[GameContext], aliases: Mapping[str, str] | None = None
) -> dict[tuple[str, str], GameContext]:
    """Index game context by ``(away_code, home_code)`` so it joins the odds board."""
    alias_map = dict(MLB_TEAM_ALIASES if aliases is None else aliases)
    codes = list(alias_map.values())
    index: dict[tuple[str, str], GameContext] = {}
    for ctx in contexts:
        home = resolve_team(ctx.home.name, codes, alias_map)
        away = resolve_team(ctx.away.name, codes, alias_map)
        if home and away:
            index[(away, home)] = ctx
    return index


def build_cards(
    events: pd.DataFrame,
    projections: Mapping[str, Any],
    lines: pd.DataFrame,
    contexts: Iterable[GameContext] = (),
    *,
    aliases: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Assemble card dicts for every resolved game on the board."""
    alias_map = dict(MLB_TEAM_ALIASES if aliases is None else aliases)
    codes = list(alias_map.values())
    ctx_by_pair = context_index(contexts, alias_map)

    cards: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        gid = str(event["game_id"])
        proj = projections.get(gid)
        if proj is None:
            continue
        away_name, home_name = str(event["away_team"]), str(event["home_team"])
        away_code = resolve_team(away_name, codes, alias_map) or away_name
        home_code = resolve_team(home_name, codes, alias_map) or home_name
        ctx = ctx_by_pair.get((away_code, home_code))
        game_lines = lines[lines["game_id"].astype(str) == gid]

        spread = float(proj.fair_spread())
        cards.append({
            "away": {
                "code": away_code, "name": away_name,
                "logo_id": ctx.away.team_id if ctx else None,
                "record": ctx.away.record if ctx else None,
            },
            "home": {
                "code": home_code, "name": home_name,
                "logo_id": ctx.home.team_id if ctx else None,
                "record": ctx.home.record if ctx else None,
            },
            "away_sp": _pitcher_dict(ctx.away_sp if ctx else None, "TBD"),
            "home_sp": _pitcher_dict(ctx.home_sp if ctx else None, "TBD"),
            "proj": {
                "away_runs": round(float(proj.mu_away), 1),
                "home_runs": round(float(proj.mu_home), 1),
                "total": round(float(proj.mu_away + proj.mu_home), 1),
                "fair_line": f"{home_code} {spread:+.1f}",
                "home_win": round(float(proj.p_home_win()) * 100),
            },
            "recs": recommend_for_game(proj, game_lines, home_code, away_code),
        })
    return cards
