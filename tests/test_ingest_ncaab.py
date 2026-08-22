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
