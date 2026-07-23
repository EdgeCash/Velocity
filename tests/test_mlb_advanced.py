"""Advanced team metrics (velocity.ingest.mlb_advanced) — pure normalizers.

Covers the FanGraphs (wRC+/xFIP) + Savant (barrel%/xwOBA) flatteners, the feed →
card-code abbreviation aliasing, the merge into one TeamAdvanced per team, and the
per-source graceful degradation the unofficial feeds demand. Network untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from velocity.ingest.mlb_advanced import (
    merge_advanced,
    normalize_fangraphs,
    normalize_savant,
)

FIXTURES = Path(__file__).parent / "fixtures"
DATA = json.loads((FIXTURES / "mlb_advanced.json").read_text())


def test_fangraphs_merges_and_aliases_codes() -> None:
    fg = normalize_fangraphs(DATA["fg_bat"], DATA["fg_pit"])
    # SFG → SF via the alias map; wRC+ and xFIP land on the same team.
    assert fg["SF"]["wrc_plus"] == 95
    assert fg["SF"]["xfip"] == pytest.approx(4.10)
    assert fg["LAD"]["wrc_plus"] == 118


def test_savant_reads_barrel_and_xwoba_fallback() -> None:
    sv = normalize_savant(DATA["savant"])
    assert sv["LAD"]["barrel_pct"] == pytest.approx(9.8)
    assert sv["LAD"]["xwoba"] == pytest.approx(0.335)
    # SFG uses est_woba (Savant's alt column) → xwoba.
    assert sv["SF"]["xwoba"] == pytest.approx(0.312)
    # TBR (→ TB) has a barrel rate but no xwOBA — only the present field survives.
    assert sv["TB"] == {"barrel_pct": pytest.approx(8.1)}


def test_merge_combines_sources() -> None:
    fg = normalize_fangraphs(DATA["fg_bat"], DATA["fg_pit"])
    sv = normalize_savant(DATA["savant"])
    idx = merge_advanced(fg, sv)
    lad = idx["LAD"]
    assert lad.wrc_plus == 118 and lad.xfip == pytest.approx(3.65)
    assert lad.barrel_pct == pytest.approx(9.8) and lad.xwoba == pytest.approx(0.335)


def test_merge_degrades_when_a_source_is_empty() -> None:
    """A dead Savant feed still yields FanGraphs-only rows, Statcast fields None."""
    fg = normalize_fangraphs(DATA["fg_bat"], DATA["fg_pit"])
    idx = merge_advanced(fg, {})
    assert idx["COL"].wrc_plus == 88
    assert idx["COL"].barrel_pct is None and idx["COL"].xwoba is None


def test_empty_everything() -> None:
    assert normalize_fangraphs(None, None) == {}
    assert normalize_savant([]) == {}
    assert merge_advanced() == {}
