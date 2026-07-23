"""MLB team stats + splits + local league ranks (velocity.ingest.mlb_stats).

Pure normalizers against frozen ``/teams/stats`` fixtures: season batting/pitching
lines, the derived rates (R/G, K%, BB%, K/9), the locally-computed league ranks
(the honest part — ranked here, not scraped), and the situational splits. Network
``load_*`` untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from velocity.ingest.mlb_stats import (
    hitting_ranks,
    normalize_team_hitting,
    normalize_team_pitching,
    normalize_team_splits,
    pitching_ranks,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _hitting():
    return normalize_team_hitting(_load("mlb_team_hitting.json"))


def _pitching():
    return normalize_team_pitching(_load("mlb_team_pitching.json"))


def test_hitting_line_and_derived_rates() -> None:
    by_id = {t.team_id: t for t in _hitting()}
    # The id-less "American League" aggregate row is dropped.
    assert set(by_id) == {"119", "115", "137"}
    lad = by_id["119"]
    assert lad.ops == pytest.approx(0.780)
    assert lad.runs_per_game == pytest.approx(5.0)  # 500 / 100
    assert lad.home_runs == 180
    assert lad.k_pct == pytest.approx(760 / 3800)  # 0.20
    assert lad.bb_pct == pytest.approx(380 / 3800)  # 0.10


def test_pitching_line_and_k9_fallback() -> None:
    by_id = {t.team_id: t for t in _pitching()}
    lad = by_id["119"]
    assert lad.era == pytest.approx(3.50)
    assert lad.k_per_9 == pytest.approx(9.50)  # native strikeoutsPer9Inn
    assert lad.runs_allowed_per_game == pytest.approx(3.80)  # 380 / 100
    # SF has no strikeoutsPer9Inn field → derived from K / IP * 9.
    sf = by_id["137"]
    assert sf.k_per_9 == pytest.approx(round(796 / 895 * 9, 2))


def test_hitting_ranks_best_is_one() -> None:
    ranks = hitting_ranks(_hitting())
    # OPS desc: LAD .780 (1), COL .740 (2), SF .700 (3).
    assert ranks["119"]["ops"] == 1
    assert ranks["115"]["ops"] == 2
    assert ranks["137"]["ops"] == 3
    # Runs/game desc: LAD 5.0 (1), COL 4.5 (2), SF 4.0 (3).
    assert ranks["119"]["rpg"] == 1
    assert ranks["137"]["rpg"] == 3


def test_pitching_ranks_lower_is_better() -> None:
    ranks = pitching_ranks(_pitching())
    # ERA asc: LAD 3.50 (1), SF 4.00 (2), COL 5.20 (3).
    assert ranks["119"]["era"] == 1
    assert ranks["137"]["era"] == 2
    assert ranks["115"]["era"] == 3
    assert ranks["119"]["whip"] == 1


def test_ranks_are_dense_with_ties() -> None:
    """Two teams tied on a stat share a rank; the next distinct value is +1."""
    from velocity.ingest.mlb_stats import TeamHitting, _rank

    teams = [
        TeamHitting("a", "A", ops=0.80, avg=None, obp=None, slg=None,
                    runs_per_game=None, home_runs=None, k_pct=None, bb_pct=None),
        TeamHitting("b", "B", ops=0.80, avg=None, obp=None, slg=None,
                    runs_per_game=None, home_runs=None, k_pct=None, bb_pct=None),
        TeamHitting("c", "C", ops=0.70, avg=None, obp=None, slg=None,
                    runs_per_game=None, home_runs=None, k_pct=None, bb_pct=None),
    ]
    ranks = _rank(teams, "ops", ascending=False)
    assert ranks == {"a": 1, "b": 1, "c": 2}


def test_splits_platoon_and_recent() -> None:
    payload = _load("mlb_team_splits.json")
    splits = normalize_team_splits(payload["platoon"], payload["recent"])
    assert splits.vs_lhp_ops == pytest.approx(0.760)
    assert splits.vs_rhp_ops == pytest.approx(0.800)
    assert splits.last_n == 15
    assert splits.last_n_runs_per_game == pytest.approx(82 / 15)


def test_splits_all_optional() -> None:
    empty = normalize_team_splits(None, None)
    assert empty.vs_lhp_ops is None and empty.last_n is None


def test_empty_payload_yields_nothing() -> None:
    assert normalize_team_hitting({}) == []
    assert normalize_team_pitching({}) == []
