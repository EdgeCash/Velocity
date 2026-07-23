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
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from velocity.ingest.mlb_advanced import TeamAdvanced
from velocity.ingest.mlb_context import GameContext, PitcherContext
from velocity.ingest.mlb_stats import (
    TeamHitting,
    TeamPitching,
    TeamSplits,
    hitting_ranks,
    pitching_ranks,
)
from velocity.ingest.mlb_weather import Weather
from velocity.report.park_factors import park_for
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


@dataclass(frozen=True)
class GridSources:
    """League-wide descriptive inputs for the stat grid — all optional, degrade to nothing.

    ``hitting``/``pitching`` are the all-30 StatsAPI lines (joined to a team by its
    StatsAPI id, so they need the game context); ``splits`` and ``advanced`` are
    keyed by the card's team code (so they attach even without context); ``weather``
    is keyed by ``game_id``.
    """

    hitting: tuple[TeamHitting, ...] = ()
    pitching: tuple[TeamPitching, ...] = ()
    splits: Mapping[str, TeamSplits] = field(default_factory=dict)
    advanced: Mapping[str, TeamAdvanced] = field(default_factory=dict)
    weather: Mapping[str, Weather] = field(default_factory=dict)


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _team_grid(
    team_id: str | None,
    code: str,
    hit: Mapping[str, TeamHitting],
    pit: Mapping[str, TeamPitching],
    hit_ranks: Mapping[str, dict[str, int]],
    pit_ranks: Mapping[str, dict[str, int]],
    sources: GridSources,
) -> dict[str, Any] | None:
    """One team's descriptive block; ``None`` only if nothing at all is available."""
    h = hit.get(team_id) if team_id else None
    p = pit.get(team_id) if team_id else None
    adv = sources.advanced.get(code)
    spl = sources.splits.get(code)
    hr_rank = hit_ranks.get(team_id or "", {})
    pt_rank = pit_ranks.get(team_id or "", {})

    bat = _drop_none({
        "ops": h.ops if h else None,
        "ops_rank": hr_rank.get("ops") or None,
        "rpg": round(h.runs_per_game, 2) if h and h.runs_per_game is not None else None,
        "rpg_rank": hr_rank.get("rpg") or None,
        "avg": h.avg if h else None,
        "hr": h.home_runs if h else None,
        "k_pct": round(h.k_pct, 3) if h and h.k_pct is not None else None,
        "bb_pct": round(h.bb_pct, 3) if h and h.bb_pct is not None else None,
        "wrc_plus": adv.wrc_plus if adv else None,
        "barrel_pct": adv.barrel_pct if adv else None,
        "xwoba": adv.xwoba if adv else None,
    })
    pitch = _drop_none({
        "era": p.era if p else None,
        "era_rank": pt_rank.get("era") or None,
        "whip": p.whip if p else None,
        "whip_rank": pt_rank.get("whip") or None,
        "k_per_9": p.k_per_9 if p else None,
        "xfip": adv.xfip if adv else None,
    })
    splits = _drop_none({
        "vs_lhp_ops": spl.vs_lhp_ops if spl else None,
        "vs_rhp_ops": spl.vs_rhp_ops if spl else None,
        "last_n": spl.last_n if spl else None,
        "last_n_rpg": round(spl.last_n_runs_per_game, 2)
        if spl and spl.last_n_runs_per_game is not None else None,
    })
    block = _drop_none({"bat": bat or None, "pit": pitch or None, "splits": splits or None})
    return block or None


def _conditions(home_code: str, weather: Weather | None) -> dict[str, Any] | None:
    """The weather + park-factor strip for a game (``None`` if neither is known)."""
    out: dict[str, Any] = {}
    if weather is not None:
        w = _drop_none({
            "temp_f": weather.temp_f, "wind_mph": weather.wind_mph,
            "wind_dir": weather.wind_dir, "precip_pct": weather.precip_pct,
            "roof": weather.roof, "indoors": weather.indoors or None,
        })
        if w:
            out["weather"] = w
    park = park_for(home_code)
    if park is not None:
        out["park"] = {"name": park.park, "runs": park.runs,
                       "hr": park.hr, "lean": park.lean}
    return out or None


def build_cards(
    events: pd.DataFrame,
    projections: Mapping[str, Any],
    lines: pd.DataFrame,
    contexts: Iterable[GameContext] = (),
    *,
    aliases: Mapping[str, str] | None = None,
    grid: GridSources | None = None,
) -> list[dict[str, Any]]:
    """Assemble card dicts for every resolved game on the board."""
    alias_map = dict(MLB_TEAM_ALIASES if aliases is None else aliases)
    codes = list(alias_map.values())
    ctx_by_pair = context_index(contexts, alias_map)

    sources = grid or GridSources()
    hit_idx = {t.team_id: t for t in sources.hitting}
    pit_idx = {t.team_id: t for t in sources.pitching}
    hit_ranks = hitting_ranks(sources.hitting) if sources.hitting else {}
    pit_ranks = pitching_ranks(sources.pitching) if sources.pitching else {}

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
        away_id = ctx.away.team_id if ctx else None
        home_id = ctx.home.team_id if ctx else None

        spread = float(proj.fair_spread())
        cards.append({
            "away": {
                "code": away_code, "name": away_name,
                "logo_id": away_id,
                "record": ctx.away.record if ctx else None,
            },
            "home": {
                "code": home_code, "name": home_name,
                "logo_id": home_id,
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
            "grid": {
                "away": _team_grid(away_id, away_code, hit_idx, pit_idx,
                                   hit_ranks, pit_ranks, sources),
                "home": _team_grid(home_id, home_code, hit_idx, pit_idx,
                                   hit_ranks, pit_ranks, sources),
            },
            "conditions": _conditions(home_code, sources.weather.get(gid)),
        })
    return cards
