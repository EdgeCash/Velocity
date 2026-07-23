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
    batting_stats: pd.DataFrame, pitching_stats: pd.DataFrame, config: RateConfig | None = None
) -> tuple[dict[str, Batter], dict[str, Pitcher]]:
    """Project season stats (Phase M2) into ``{player_id: Batter/Pitcher}`` pools.

    ``batting_stats`` / ``pitching_stats`` are ``BaseballStats`` frames (role
    ``bat`` / ``pit``). Batters carry both the PA-outcome vector and the
    ball-in-play profile; pitchers carry the PA-outcome vector.
    """
    config = config or RateConfig()
    pa = project_pa_rates(batting_stats, config).set_index("player_id")
    bip = project_bip_profile(batting_stats, config).set_index("player_id")

    batters: dict[str, Batter] = {}
    for pid in pa.index:
        pa_vec = np.array([pa.loc[pid, o] for o in PA_OUTCOMES], dtype=float)
        if pid in bip.index:
            bip_vec = np.array([bip.loc[pid, o] for o in BIP_OUTCOMES], dtype=float)
        else:  # a batter with no balls in play — fall back to the league profile
            bip_vec = np.array([DEFAULT_BIP_PRIOR[o] for o in BIP_OUTCOMES], dtype=float)
        batters[str(pid)] = Batter(player_id=str(pid), pa=pa_vec, bip=bip_vec)

    ppa = project_pa_rates(pitching_stats, config).set_index("player_id")
    pitchers: dict[str, Pitcher] = {
        str(pid): Pitcher(str(pid), np.array([ppa.loc[pid, o] for o in PA_OUTCOMES], dtype=float))
        for pid in ppa.index
    }
    return batters, pitchers


def _build_team(
    order: Sequence[str],
    pitcher_id: str | None,
    batters: Mapping[str, Batter],
    pitchers: Mapping[str, Pitcher],
) -> Team:
    """One club's :class:`Team`, filling any gap with a league-average stand-in."""
    lineup: list[Batter] = [batters.get(pid) or _avg_batter(pid) for pid in order]
    while len(lineup) < 9:  # unposted or short lineup — pad to a full order
        lineup.append(_avg_batter(f"fill{len(lineup)}"))
    lineup = lineup[:9]
    pitcher = pitchers.get(pitcher_id) if pitcher_id else None
    if pitcher is None:
        pitcher = _avg_pitcher(pitcher_id or "unknown")
    return Team(lineup=lineup, pitcher=pitcher)


def assemble_model(
    lineups: Iterable[GameLineups],
    batters: Mapping[str, Batter],
    pitchers: Mapping[str, Pitcher],
    *,
    aliases: Mapping[str, str] | None = None,
    config: BaseballSimConfig | None = None,
    seed: int = 0,
) -> tuple[MLBGameModel, list[str]]:
    """Build an :class:`MLBGameModel` keyed by rating code from parsed lineups.

    Each club's provider name is resolved to a rating code via ``aliases``; the
    model's teams use those keys, matching what the slate resolver produces.
    Returns the model plus any team names that did not resolve.
    """
    # Local import keeps the models layer from importing wagering at module load.
    from velocity.wagering.live import MLB_TEAM_ALIASES, resolve_team

    alias_map = dict(MLB_TEAM_ALIASES if aliases is None else aliases)
    codes = list(alias_map.values())
    config = config or BaseballSimConfig()

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
            teams[code] = _build_team(order, pitcher_id, batters, pitchers)

    return MLBGameModel(teams=teams, config=config, seed=seed), unresolved


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
    from velocity.models.simulate_baseball import DEFAULT_HFA
    from velocity.wagering.props_slate import build_name_index

    batting = load_player_stats(season, "bat")
    pitching = load_player_stats(season, "pit")
    batters, pitchers = build_player_pools(batting, pitching)
    names = build_name_index(batting, pitching)
    config = config or BaseballSimConfig(n_sims=10_000, starter_outs=18, hfa=DEFAULT_HFA)
    model, _ = assemble_model(load_lineups(date), batters, pitchers, config=config, seed=seed)
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
