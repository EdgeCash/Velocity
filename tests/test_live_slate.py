"""Live-slate orchestration — provider snapshot → staked recommendations, offline.

Exercises the pure pieces (team resolution, side canonicalization, event
extraction, the exclude_closing live path) plus the end-to-end orchestration on a
canned Odds API snapshot with a tiny hand-built model — no network, deterministic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from velocity.features.scores import fit_scores_ratings
from velocity.ingest.theoddsapi import extract_events, normalize_odds_events
from velocity.models.game_scores import ScoresGameModel
from velocity.util.seed import make_rng
from velocity.wagering.live import (
    NFL_TEAM_ALIASES,
    build_live_slate,
    canonicalize_sides,
    resolve_team,
    slate_to_frame,
)
from velocity.wagering.slate import SlateConfig, build_slate

KNOWN_NFL = ("KC", "BUF", "SF", "PHI")


def test_resolve_exact_alias_and_fuzzy() -> None:
    # exact key
    assert resolve_team("KC", KNOWN_NFL) == "KC"
    # alias table (full name → abbrev)
    assert resolve_team("Kansas City Chiefs", KNOWN_NFL) == "KC"
    assert resolve_team("Buffalo Bills", KNOWN_NFL) == "BUF"
    # normalized fallback against known keys with punctuation/casing drift
    assert resolve_team("s.f.", ("S F",)) == "S F"


def test_resolve_returns_none_on_miss() -> None:
    assert resolve_team("Nonexistent Team", KNOWN_NFL) is None
    # alias resolves to an abbrev the model doesn't know → None, not a guess
    assert resolve_team("Dallas Cowboys", KNOWN_NFL) is None


def test_alias_table_covers_32_teams() -> None:
    assert len(NFL_TEAM_ALIASES) == 32
    assert len(set(NFL_TEAM_ALIASES.values())) == 32


# --- a canned Odds API snapshot: two NFL events, three markets each -----------

def _event(eid, home, away, home_ml, away_ml, home_pt, spread_price, total, over_p, under_p):
    return {
        "id": eid,
        "sport_key": "americanfootball_nfl",
        "commence_time": "2026-09-13T17:00:00Z",
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": "draftkings",
                "last_update": "2026-09-13T16:30:00Z",
                "markets": [
                    {"key": "h2h", "outcomes": [
                        {"name": home, "price": home_ml},
                        {"name": away, "price": away_ml},
                    ]},
                    {"key": "spreads", "outcomes": [
                        {"name": home, "price": spread_price, "point": home_pt},
                        {"name": away, "price": spread_price, "point": -home_pt},
                    ]},
                    {"key": "totals", "outcomes": [
                        {"name": "Over", "price": over_p, "point": total},
                        {"name": "Under", "price": under_p, "point": total},
                    ]},
                ],
            }
        ],
    }


SNAPSHOT = [
    _event("g1", "Kansas City Chiefs", "Buffalo Bills", -150, 130, -3.0, -110, 47.5, -110, -110),
    _event(
        "g2", "San Francisco 49ers", "Philadelphia Eagles",
        120, -140, 2.5, -110, 44.5, -110, -110,
    ),
]


def test_extract_events_gives_games_frame() -> None:
    ev = extract_events(SNAPSHOT)
    assert list(ev["game_id"]) == ["g1", "g2"]
    assert ev["home_team"].tolist() == ["Kansas City Chiefs", "San Francisco 49ers"]
    # kickoff parsed to a naive datetime (resolution-agnostic)
    assert ev["kickoff"].dtype.kind == "M"
    assert ev["kickoff"].notna().all()


def test_canonicalize_sides_maps_team_names_and_totals() -> None:
    lines = normalize_odds_events(SNAPSHOT)
    events = extract_events(SNAPSHOT)
    canon = canonicalize_sides(lines, events)
    assert set(canon["side"]) == {"home", "away", "over", "under"}
    # every original row maps (nothing dropped for this clean snapshot)
    assert len(canon) == len(lines)
    # g1 home rows correspond to the Chiefs
    g1_home = canon[(canon["game_id"] == "g1") & (canon["side"] == "home")]
    assert not g1_home.empty


def _tiny_nfl_model() -> ScoresGameModel:
    # Fit scores ratings on a tiny synthetic history over the four known teams so
    # projections are deterministic and the teams resolve.
    rng = np.random.default_rng(0)
    rows = []
    teams = list(KNOWN_NFL)
    gid = 0
    for _ in range(6):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                rows.append({
                    "game_id": f"s{gid}", "season": 2025, "week": 1, "season_type": "REG",
                    "kickoff": pd.Timestamp("2025-09-01"), "league": "nfl",
                    "home_team": h, "away_team": a, "neutral_site": False,
                    "roof": None, "surface": None,
                    "home_score": float(rng.integers(17, 31)),
                    "away_score": float(rng.integers(14, 28)),
                })
                gid += 1
    games = pd.DataFrame(rows)
    return ScoresGameModel(fit_scores_ratings(games))


def test_build_live_slate_end_to_end() -> None:
    model = _tiny_nfl_model()
    lines = normalize_odds_events(SNAPSHOT)
    events = extract_events(SNAPSHOT)

    def project(home: str, away: str):
        return model.project(home, away, rng=make_rng(seed=1))

    log, unresolved = build_live_slate(
        events, lines, project, model.ratings.teams,
        SlateConfig(exclude_closing=False, min_edge=0.0),
    )
    # All four teams are known → nothing unresolved.
    assert unresolved == []
    frame = slate_to_frame(log)
    # With min_edge=0 the engine should surface at least some staked bets.
    assert isinstance(frame, pd.DataFrame)
    assert set(frame.columns) >= {"game_id", "market", "side", "price", "stake", "p_model"}


def test_build_live_slate_reports_unresolved() -> None:
    model = _tiny_nfl_model()
    snap = [
        _event("gX", "Detroit Lions", "Chicago Bears", -110, -110, -1.0, -110, 45.5, -110, -110)
    ]
    lines = normalize_odds_events(snap)
    events = extract_events(snap)
    log, unresolved = build_live_slate(
        events, lines, lambda h, a: model.project(h, a), model.ratings.teams,
    )
    # Neither DET nor CHI is in the tiny model → the game is skipped and reported.
    assert len(unresolved) == 1
    assert unresolved[0]["game_id"] == "gX"
    assert len(log) == 0


def test_exclude_closing_flag_live_vs_backtest() -> None:
    # One game, one pre-kickoff snapshot. In backtest mode the lone observation is
    # treated as the close and excluded (no entries); in live mode it's kept.
    lines = normalize_odds_events(SNAPSHOT)
    events = extract_events(SNAPSHOT)
    canon = canonicalize_sides(lines, events)
    games = events[["game_id", "kickoff"]].copy()

    model = _tiny_nfl_model()
    projections = {
        "g1": model.project("KC", "BUF", rng=make_rng(seed=2)),
        "g2": model.project("SF", "PHI", rng=make_rng(seed=3)),
    }
    live_cfg = SlateConfig(exclude_closing=False, min_edge=0.0)
    bt_cfg = SlateConfig(exclude_closing=True, min_edge=0.0)
    live = build_slate(projections, canon, games, live_cfg)
    backtest = build_slate(projections, canon, games, bt_cfg)
    assert len(backtest) == 0  # sole observation excluded as "closing"
    assert len(live) >= len(backtest)
