"""Assemble a live MLB model from StatsAPI stats + lineups.

The final live-data wiring: turn season stats into projected per-PA rates
(Phase M2), turn a probable-lineups payload into batting orders and starters
(:func:`velocity.ingest.mlb.normalize_lineups`), and stitch them into an
:class:`~velocity.models.game_mlb.MLBGameModel` keyed by rating code — the same
keys the slate resolver produces from provider team names.

Split into a pure, offline-tested core (:func:`build_player_pools`,
:func:`assemble_model`) and a thin network orchestrator
(:func:`build_live_mlb_model`). A player with no season stats, an unposted lineup,
or an unannounced starter falls back to a league-average stand-in, so a partial
or early board still produces a full model rather than failing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from velocity.features.baseball import (
    BIP_OUTCOMES,
    DEFAULT_BAT_PRIOR,
    DEFAULT_BIP_PRIOR,
    DEFAULT_PIT_PRIOR,
    PA_OUTCOMES,
    RateConfig,
    project_bip_profile,
    project_pa_rates,
)
from velocity.ingest.mlb import GameLineups
from velocity.models.game_mlb import MLBGameModel
from velocity.models.simulate_baseball import (
    BaseballSimConfig,
    Batter,
    Pitcher,
    Team,
    batter_from_rates,
    pitcher_from_rates,
)


def _avg_batter(player_id: str) -> Batter:
    return batter_from_rates(player_id, DEFAULT_BAT_PRIOR, DEFAULT_BIP_PRIOR)


def _avg_pitcher(player_id: str) -> Pitcher:
    return pitcher_from_rates(player_id, DEFAULT_PIT_PRIOR)


def build_player_pools(
    batting_stats: pd.DataFrame,
    pitching_stats: pd.DataFrame,
    config: RateConfig | None = None,
    hands: Mapping[str, Mapping[str, str | None]] | None = None,
) -> tuple[dict[str, Batter], dict[str, Pitcher]]:
    """Project season stats (Phase M2) into ``{player_id: Batter/Pitcher}`` pools.

    ``batting_stats`` / ``pitching_stats`` are ``BaseballStats`` frames (role
    ``bat`` / ``pit``). Batters carry both the PA-outcome vector and the
    ball-in-play profile; pitchers carry the PA-outcome vector. ``hands`` (from
    :func:`velocity.ingest.mlb_people.normalize_player_hands`, ``{id: {bat, pit}}``)
    tags each with a batting side / throwing hand for the platoon matchup; a player
    absent from it stays ``None`` (platoon-neutral).
    """
    config = config or RateConfig()
    hands = hands or {}
    pa = project_pa_rates(batting_stats, config).set_index("player_id")
    bip = project_bip_profile(batting_stats, config).set_index("player_id")

    batters: dict[str, Batter] = {}
    for pid in pa.index:
        pa_vec = np.array([pa.loc[pid, o] for o in PA_OUTCOMES], dtype=float)
        if pid in bip.index:
            bip_vec = np.array([bip.loc[pid, o] for o in BIP_OUTCOMES], dtype=float)
        else:  # a batter with no balls in play — fall back to the league profile
            bip_vec = np.array([DEFAULT_BIP_PRIOR[o] for o in BIP_OUTCOMES], dtype=float)
        bat_hand = hands.get(str(pid), {}).get("bat")
        batters[str(pid)] = Batter(player_id=str(pid), pa=pa_vec, bip=bip_vec, hand=bat_hand)

    ppa = project_pa_rates(pitching_stats, config).set_index("player_id")
    pitchers: dict[str, Pitcher] = {
        str(pid): Pitcher(
            str(pid),
            np.array([ppa.loc[pid, o] for o in PA_OUTCOMES], dtype=float),
            hand=hands.get(str(pid), {}).get("pit"),
        )
        for pid in ppa.index
    }
    return batters, pitchers


def _build_team(
    order: Sequence[str],
    pitcher_id: str | None,
    batters: Mapping[str, Batter],
    pitchers: Mapping[str, Pitcher],
    bullpen: Pitcher | None = None,
) -> Team:
    """One club's :class:`Team`, filling any gap with a league-average stand-in."""
    lineup: list[Batter] = [batters.get(pid) or _avg_batter(pid) for pid in order]
    while len(lineup) < 9:  # unposted or short lineup — pad to a full order
        lineup.append(_avg_batter(f"fill{len(lineup)}"))
    lineup = lineup[:9]
    pitcher = pitchers.get(pitcher_id) if pitcher_id else None
    if pitcher is None:
        pitcher = _avg_pitcher(pitcher_id or "unknown")
    return Team(lineup=lineup, pitcher=pitcher, bullpen=bullpen)


def bullpens_from_rates(
    rate_map: Mapping[str, Mapping[str, float]],
) -> dict[str, Pitcher]:
    """Turn ``{team_code: PA-rate dict}`` (from mlb_bullpen) into reliever Pitchers."""
    return {
        code: pitcher_from_rates(f"{code}_pen", rates) for code, rates in rate_map.items()
    }


def assemble_model(
    lineups: Iterable[GameLineups],
    batters: Mapping[str, Batter],
    pitchers: Mapping[str, Pitcher],
    *,
    aliases: Mapping[str, str] | None = None,
    config: BaseballSimConfig | None = None,
    seed: int = 0,
    park_hr_factors: Mapping[str, float] | None = None,
    run_env_tilts: Mapping[str, float] | None = None,
    bullpens: Mapping[str, Pitcher] | None = None,
) -> tuple[MLBGameModel, list[str]]:
    """Build an :class:`MLBGameModel` keyed by rating code from parsed lineups.

    Each club's provider name is resolved to a rating code via ``aliases``; the
    model's teams use those keys, matching what the slate resolver produces.
    ``park_hr_factors`` (home-park HR multipliers) and ``run_env_tilts`` (the non-HR
    run-environment tilt) by code make each game's total park/weather-aware;
    ``bullpens`` (a reliever :class:`Pitcher` by code) finishes each game with the
    real pen instead of the league-average stand-in. Returns the model plus any team
    names that did not resolve.
    """
    # Local import keeps the models layer from importing wagering at module load.
    from velocity.wagering.live import MLB_TEAM_ALIASES, resolve_team

    alias_map = dict(MLB_TEAM_ALIASES if aliases is None else aliases)
    codes = list(alias_map.values())
    config = config or BaseballSimConfig()
    pen_by_code = dict(bullpens or {})

    teams: dict[str, Team] = {}
    unresolved: list[str] = []
    for game in lineups:
        sides = (
            (game.home_team, game.home_pitcher_id, game.home_lineup),
            (game.away_team, game.away_pitcher_id, game.away_lineup),
        )
        for team_name, pitcher_id, order in sides:
            code = resolve_team(team_name, codes, alias_map)
            if code is None:
                unresolved.append(team_name)
                continue
            teams[code] = _build_team(
                order, pitcher_id, batters, pitchers, pen_by_code.get(code)
            )

    model = MLBGameModel(
        teams=teams, config=config, seed=seed,
        park_hr_factors=dict(park_hr_factors or {}),
        run_env_tilts=dict(run_env_tilts or {}),
    )
    return model, unresolved


def build_live_mlb(
    date: str,
    season: int,
    *,
    config: BaseballSimConfig | None = None,
    seed: int = 0,
) -> tuple[MLBGameModel, dict[str, str]]:  # pragma: no cover - network
    """Fetch StatsAPI stats + lineups and return (model, player-name → id index).

    The name index is what the prop slate uses to resolve a provider player name
    to a model player id.
    """
    from velocity.ingest.mlb import load_lineups, load_player_stats
    from velocity.ingest.mlb_bullpen import load_team_bullpen
    from velocity.ingest.mlb_people import load_player_hands
    from velocity.models.simulate_baseball import (
        DEFAULT_HFA,
        DEFAULT_PLATOON_GAP,
        DEFAULT_TTO_PENALTY,
    )
    from velocity.report.park_factors import run_environment_maps
    from velocity.wagering.props_slate import build_name_index

    batting = load_player_stats(season, "bat")
    pitching = load_player_stats(season, "pit")
    lineups = list(load_lineups(date))
    # Handedness for just the players in today's games (lineups + probable starters).
    ids: set[str] = set()
    for g in lineups:
        ids.update(g.home_lineup)
        ids.update(g.away_lineup)
        ids.update(p for p in (g.home_pitcher_id, g.away_pitcher_id) if p)
    try:
        hands = load_player_hands(ids)
    except Exception as exc:  # noqa: BLE001 - hands are optional; platoon stays neutral
        print(f"player hands skipped ({exc}); platoon neutral")
        hands = {}
    batters, pitchers = build_player_pools(batting, pitching, hands=hands)
    names = build_name_index(batting, pitching)
    config = config or BaseballSimConfig(
        n_sims=10_000, starter_outs=18, hfa=DEFAULT_HFA,
        tto_penalty=DEFAULT_TTO_PENALTY, platoon_gap=DEFAULT_PLATOON_GAP,
    )
    # Park-static run environment; the runner folds today's weather in before pricing.
    hr_factors, run_env_tilts = run_environment_maps()
    try:  # real per-team bullpen; degrade to the starter stand-in if the feed fails
        bullpens = bullpens_from_rates(load_team_bullpen(season))
    except Exception as exc:  # noqa: BLE001 - unofficial feed; the sim falls back cleanly
        print(f"bullpen rates skipped ({exc}); using the starter stand-in")
        bullpens = {}
    model, _ = assemble_model(
        lineups, batters, pitchers,
        config=config, seed=seed, park_hr_factors=hr_factors,
        run_env_tilts=run_env_tilts, bullpens=bullpens,
    )
    return model, names


def build_live_mlb_model(
    date: str,
    season: int,
    *,
    config: BaseballSimConfig | None = None,
    seed: int = 0,
) -> MLBGameModel:  # pragma: no cover - network
    """Fetch season stats + today's lineups from StatsAPI and assemble the model."""
    model, _ = build_live_mlb(date, season, config=config, seed=seed)
    return model
