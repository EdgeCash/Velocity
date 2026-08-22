"""NCAAB ingest — Torvik payload shapes normalize to the canonical ratings frame.

The fixtures are frozen from the live endpoints (2026-08): the CSV serves a
header row; the JSON (live and timemachine) serves the same rows positionally
in the CSV's column order.
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.ingest.ncaab import (
    TEAM_RESULTS_COLUMNS,
    expected_matchup,
    normalize_team_results,
    team_results_url,
    timemachine_url,
)


def _row(rank: int, team: str, adj_o: float, adj_d: float, adj_t: float,
         barthag: float) -> list[object]:
    """One positional row shaped like the wire format (adjt is the last column)."""
    row: list[object] = [None] * len(TEAM_RESULTS_COLUMNS)
    row[0] = rank
    row[1] = team
    row[2] = "B12"
    row[3] = "35-5"
    row[4] = adj_o
    row[6] = adj_d
    row[8] = barthag
    row[41] = 11.5  # WAB
    row[-1] = adj_t
    return row


# Values frozen from the live 2025 endpoint (Houston #1, Duke #2).
PAYLOAD = [
    _row(1, "Houston", 124.69232186200061, 87.34753529472775, 61.78137219905102,
         0.9835925505281423),
    _row(2, "Duke", 130.5679429123398, 92.01405731448614, 65.96334886563857,
         0.9824406297611048),
    _row(3, "Short Row", 100.0, 100.0, 65.0, 0.5)[:9],  # early-season truncation
    "not a row",  # malformed entries contribute nothing
]


def test_positional_json_normalizes_to_ratings() -> None:
    out = normalize_team_results(PAYLOAD, season=2025)
    assert list(out["team"]) == ["Houston", "Duke", "Short Row"]
    assert out.loc[0, "adj_o"] == pytest.approx(124.6923, abs=1e-3)
    assert out.loc[0, "adj_d"] == pytest.approx(87.3475, abs=1e-3)
    assert out.loc[0, "adj_t"] == pytest.approx(61.7814, abs=1e-3)
    assert out.loc[0, "barthag"] == pytest.approx(0.9836, abs=1e-3)
    assert out.loc[0, "wab"] == pytest.approx(11.5)
    assert (out["season"] == 2025).all()
    # The truncated row keeps what it has; its missing tempo is NA, not zero.
    assert pd.isna(out.loc[2, "adj_t"])


def test_header_keyed_frame_normalizes_identically() -> None:
    frame = pd.DataFrame(
        [_row(1, "Houston", 124.69, 87.35, 61.78, 0.98)],
        columns=list(TEAM_RESULTS_COLUMNS),
    )
    out = normalize_team_results(frame, as_of="2025-02-01")
    assert out.loc[0, "team"] == "Houston"
    assert out.loc[0, "adj_o"] == pytest.approx(124.69)
    assert out.loc[0, "as_of"] == pd.Timestamp("2025-02-01")


def test_rows_without_efficiency_are_dropped() -> None:
    incomplete = [_row(1, "Ghost Team", float("nan"), float("nan"), 65.0, 0.5)]
    assert normalize_team_results(incomplete).empty


def test_expected_matchup_possession_math() -> None:
    ratings = normalize_team_results([
        _row(1, "A", 110.0, 90.0, 70.0, 0.9),
        _row(2, "B", 100.0, 100.0, 66.0, 0.5),
    ])
    out = expected_matchup(ratings, "A", "B", hca_points=3.0)
    assert out is not None
    # avg_o = 105; pace = 68; A: 110·100/105 = 104.76 pp100, B: 100·90/105.
    assert out["pace"] == pytest.approx(68.0)
    assert out["home_points"] == pytest.approx(110.0 * 100.0 / 105.0 * 0.68 + 1.5)
    assert out["away_points"] == pytest.approx(100.0 * 90.0 / 105.0 * 0.68 - 1.5)
    assert out["total"] == pytest.approx(out["home_points"] + out["away_points"])
    # An unknown team is never guessed.
    assert expected_matchup(ratings, "A", "Nowhere State") is None


def test_endpoint_urls() -> None:
    assert team_results_url(2025).endswith("/2025_team_results.json")
    assert timemachine_url("2025-02-01").endswith(
        "/timemachine/team_results/20250201_team_results.json.gz"
    )


# ---------------------------------------------------------------------------
# hoopR schedules/boxes and the Torvik pseudo-games prior (phase N2).
# ---------------------------------------------------------------------------

from velocity.ingest.ncaab import (  # noqa: E402
    PRIOR_ANCHOR,
    ncaab_week,
    normalize_hoopr_schedule,
    normalize_hoopr_team_box,
    torvik_pseudo_games,
    torvik_team_candidates,
)


def test_ncaab_week_is_nov_anchored_and_monotone() -> None:
    # Season 2025 = 2024-25: Nov 1 is week 0 and January sorts AFTER December
    # (the day-of-year convention would invert here).
    assert ncaab_week(pd.Timestamp("2024-11-01"), 2025) == 0
    dec = ncaab_week(pd.Timestamp("2024-12-30"), 2025)
    jan = ncaab_week(pd.Timestamp("2025-01-02"), 2025)
    apr = ncaab_week(pd.Timestamp("2025-04-07"), 2025)
    assert dec <= jan <= apr
    assert apr == 10
    # Clamped into the schema's 0-25 in both directions.
    assert ncaab_week(pd.Timestamp("2024-05-01"), 2025) == 0
    assert ncaab_week(pd.Timestamp("2026-06-01"), 2025) == 25


def _sched_row(gid: str, home: str = "Duke", away: str = "Houston",
               completed: bool = True, season_type: int = 2) -> dict[str, object]:
    return {
        "game_id": gid, "home_location": home, "away_location": away,
        "status_type_completed": completed, "season_type": season_type,
        "game_date_time": "2025-01-02T00:00:00", "neutral_site": False,
        "home_score": 80, "away_score": 71,
    }


def test_normalize_hoopr_schedule_keeps_completed_regular_games() -> None:
    raw = pd.DataFrame([
        _sched_row("1"),
        _sched_row("2", completed=False),        # in progress → dropped
        _sched_row("3", season_type=1),          # exhibition → dropped
        _sched_row("4", home=""),                # missing team → dropped
        _sched_row("1"),                         # duplicate id → deduped
        _sched_row("5", season_type=3),          # tournament → kept as POST
    ])
    out = normalize_hoopr_schedule(raw, season=2025)
    assert sorted(out["game_id"]) == ["1", "5"]
    row = out.set_index("game_id").loc["1"]
    assert row["league"] == "ncaab"
    assert row["season"] == 2025
    assert row["week"] == ncaab_week(pd.Timestamp("2025-01-02"), 2025)
    assert row["home_team"] == "Duke"
    assert row["home_score"] == 80.0
    assert out.set_index("game_id").loc["5", "season_type"] == "POST"


def test_normalize_hoopr_team_box_slims_to_possession_components() -> None:
    raw = pd.DataFrame([{
        "game_id": 401, "team_home_away": "home", "field_goals_attempted": "60",
        "offensive_rebounds": 10, "total_turnovers": 12,
        "free_throws_attempted": 20, "team_score": 80, "extra": "dropped",
    }])
    out = normalize_hoopr_team_box(raw, season=2025)
    assert list(out.columns) == [
        "game_id", "team_home_away", "field_goals_attempted",
        "offensive_rebounds", "total_turnovers", "free_throws_attempted",
        "season",
    ]
    assert out.loc[0, "game_id"] == "401"
    assert out.loc[0, "field_goals_attempted"] == 60.0


def test_torvik_team_candidates_expands_st_but_not_saints() -> None:
    assert "Michigan State" in torvik_team_candidates("Michigan St.")
    # Leading St. is Saint, never State.
    assert torvik_team_candidates("St. John's") == ("St. John's",)
    # Hand-checked aliases resolve ahead of the literal name.
    assert torvik_team_candidates("UMKC")[0] == "Kansas City"
    assert torvik_team_candidates("Grambling St.")[0] == "Grambling"


def _torvik_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {"team": "Duke", "adj_o": 120.0, "adj_d": 90.0, "adj_t": 68.0, "season": 2025},
        {"team": "Michigan St.", "adj_o": 110.0, "adj_d": 95.0, "adj_t": 64.0,
         "season": 2025},
        {"team": "Not A School", "adj_o": 100.0, "adj_d": 100.0, "adj_t": 65.0,
         "season": 2025},
    ])


def test_torvik_pseudo_games_encode_ratings_exactly() -> None:
    teams = {"Duke", "Michigan State"}
    games, pace = torvik_pseudo_games(
        _torvik_frame(), teams, cutoff=pd.Timestamp("2025-11-05"), k=3
    )
    # k copies per matched team; the unmatched school contributes nothing.
    assert len(games) == 6 and len(pace) == 6
    assert set(games["home_team"]) == teams
    assert (games["away_team"] == PRIOR_ANCHOR).all()
    assert games["neutral_site"].all()
    assert (games["season"] == 2026).all()
    assert (games["week"] == 0).all()
    # The per-100 identity: score / poss × 100 recovers adj_o / adj_d.
    duke = games[games["home_team"] == "Duke"].merge(pace, on="game_id")
    assert (duke["home_score"] / duke["poss"] * 100.0).tolist() == pytest.approx(
        [120.0] * 3
    )
    assert (duke["away_score"] / duke["poss"] * 100.0).tolist() == pytest.approx(
        [90.0] * 3
    )
    assert duke["poss"].tolist() == pytest.approx([68.0] * 3)


def test_torvik_pseudo_games_leak_gate() -> None:
    teams = {"Duke", "Michigan State"}
    # Mid-season 2025 (torvik-2025 not final until April) → no pseudo-games.
    games, _ = torvik_pseudo_games(
        _torvik_frame(), teams, cutoff=pd.Timestamp("2025-02-01"), k=3
    )
    assert games.empty
    # After the season closes the prior unlocks.
    games, _ = torvik_pseudo_games(
        _torvik_frame(), teams, cutoff=pd.Timestamp("2025-04-10"), k=1
    )
    assert len(games) == 2
