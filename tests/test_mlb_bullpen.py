"""Per-team bullpen rates (velocity.ingest.mlb_bullpen) — pure normalizer.

Covers the FanGraphs team-reliever flattening: rate fields vs count/TBF fallback,
percent-vs-fraction tolerance, feed→card-code aliasing, and a valid PA vector that
sums to 1. Network untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from velocity.features.baseball import PA_OUTCOMES
from velocity.ingest.mlb_bullpen import normalize_team_bullpen

FIXTURES = Path(__file__).parent / "fixtures"
DATA = normalize_team_bullpen(json.loads((FIXTURES / "mlb_bullpen.json").read_text()))


def test_rates_from_percent_fields() -> None:
    lad = DATA["LAD"]
    assert lad["k"] == pytest.approx(0.26)  # K% given as a fraction
    assert lad["bb"] == pytest.approx(0.075)
    assert lad["hbp"] == pytest.approx(18 / 2000)
    assert lad["hr"] == pytest.approx(0.028)


def test_percent_scale_is_normalized() -> None:
    # SFG gives K%/BB% as whole-number percents (22.0, 9.0) → fractions.
    sf = DATA["SF"]  # SFG → SF via alias
    assert sf["k"] == pytest.approx(0.22)
    assert sf["bb"] == pytest.approx(0.09)
    assert sf["hr"] == pytest.approx(45 / 1950)  # HR count / TBF fallback


def test_rates_from_counts_fallback() -> None:
    col = DATA["COL"]
    assert col["k"] == pytest.approx(420 / 2100)  # SO / TBF
    assert col["bb"] == pytest.approx(210 / 2100)
    assert col["hr"] == pytest.approx(70 / 2100)


def test_vector_is_a_valid_distribution() -> None:
    for code, vec in DATA.items():
        assert set(vec) == set(PA_OUTCOMES), code
        assert sum(vec.values()) == pytest.approx(1.0), code
        assert all(v >= 0 for v in vec.values()), code


def test_empty_payload_yields_nothing() -> None:
    assert normalize_team_bullpen({}) == {}
    assert normalize_team_bullpen({"data": [{"Team": "LAD"}]}) == {}  # no TBF → skipped
