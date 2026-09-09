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


# --- neutral sites -----------------------------------------------------------
# The board never says where a game is played; the league schedule does, and
# every model wrapper takes the flag — it simply never reached them live
# (docs/SYSTEM_REVIEW.md §3.2).


def _neutral_events() -> pd.DataFrame:
    return pd.DataFrame({
        "game_id": ["melb", "ordinary", "unknown"],
        "home_team": ["Los Angeles Rams", "Kansas City Chiefs", "Buffalo Bills"],
        "away_team": ["San Francisco 49ers", "Denver Broncos", "Miami Dolphins"],
        "kickoff": pd.to_datetime(["2026-09-11 00:35", "2026-09-14 20:20", "2026-09-13 17:00"]),
    })


def _neutral_schedule() -> pd.DataFrame:
    # The schedule lists the Melbourne game with the sides swapped relative to
    # the board and its kickoff in local time — a day and an orientation away
    # from the board's UTC row, both of which the matcher must absorb.
    return pd.DataFrame({
        "game_id": ["2026_01_SF_LA", "2026_02_DEN_KC"],
        "home_team": ["SF", "KC"],
        "away_team": ["LA", "DEN"],
        "kickoff": pd.to_datetime(["2026-09-10 20:35", "2026-09-14 20:20"]),
        "neutral_site": [True, False],
    })


def _projection(home: str, away: str) -> object:
    # project_board never inspects the projection — any object stands in.
    return (home, away)


def test_neutral_site_map_matches_either_orientation_within_the_window() -> None:
    from velocity.wagering.live import neutral_site_map

    known = ["LA", "SF", "KC", "DEN", "BUF", "MIA"]
    flags = neutral_site_map(_neutral_events(), _neutral_schedule(), known)
    assert flags == {"melb": True, "ordinary": False}
    # A board game the schedule does not carry is absent — never guessed.
    assert "unknown" not in flags
    # No schedule, no opinion.
    assert neutral_site_map(_neutral_events(), None, known) == {}
    assert neutral_site_map(_neutral_events(), pd.DataFrame(), known) == {}


def test_project_board_hands_the_flag_only_to_projectors_that_take_it() -> None:
    from velocity.wagering.live import project_board

    seen: list[tuple[str, str, bool]] = []

    def aware(home: str, away: str, neutral_site: bool = False):  # noqa: ANN202
        seen.append((home, away, neutral_site))
        return _projection(home, away)

    known = ["LA", "SF", "KC", "DEN", "BUF", "MIA"]
    projections, _ = project_board(
        _neutral_events(), aware, known, neutral_by_game={"melb": True, "ordinary": False}
    )
    assert set(projections) == {"melb", "ordinary", "unknown"}
    assert ("LA", "SF", True) in seen
    assert ("KC", "DEN", False) in seen
    assert ("BUF", "MIA", False) in seen  # absent from the map → home field as before

    def blind(home: str, away: str):  # noqa: ANN202
        return _projection(home, away)

    projections, _ = project_board(
        _neutral_events(), blind, known, neutral_by_game={"melb": True}
    )
    assert len(projections) == 3  # a projector without the parameter is untouched
