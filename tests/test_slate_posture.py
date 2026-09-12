"""Staking posture — the two calls that were the operator's to make.

Neither of these was a bug. They were earlier decisions that had stopped
matching the evidence behind them, and they are recorded here as code so a
later reader can see what was chosen and why.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts" / "run_live_slate.py"


def _runner():
    spec = importlib.util.spec_from_file_location("run_live_slate", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(**kw) -> argparse.Namespace:
    base = {"league": "ncaaf", "paper": None, "team_totals_paper": True,
            "ncaaf_spreads": False, "ncaaf_moneylines": False,
            "model_weight": None}
    return argparse.Namespace(**{**base, **kw})


# --- MLB: anchored to the market ---------------------------------------------


def test_mlb_is_anchored_to_the_market() -> None:
    """It had been running raw, on the league carrying the largest exposure.

    No anchor and no probability shrink, while the lab's own MLB rounds put
    the model at Brier 0.2485 against the de-vigged closing moneyline's
    0.2491 — parity, not an edge. 0.2 is the NFL's anchor, taken as a holding
    position rather than a fitted one: the sweep that would choose it properly
    needs the private closing-moneyline archive, and has to be re-run anyway
    because the evidence above came from the sim counts.py replaced.
    """
    runner = _runner()
    assert runner.resolve_model_weight(None, "mlb") == 0.2
    assert runner.resolve_model_weight(None, "nfl") == 0.2
    assert runner.resolve_model_weight(None, "ncaaf") == 0.13


def test_an_explicit_weight_still_wins() -> None:
    runner = _runner()
    assert runner.resolve_model_weight(1.0, "mlb") == 1.0


def test_a_league_with_no_entry_is_still_raw() -> None:
    # Anchoring is opt-in per league; an unlisted one prices off the model.
    runner = _runner()
    assert runner.resolve_model_weight(None, "nhl") == 1.0


# --- NCAAF sides: papered, not excluded --------------------------------------


def test_the_college_sides_are_papered_rather_than_dropped() -> None:
    """Excluded produced no row at all — no price, no CLV, nothing to grade.

    A market that is never recorded can never be re-tested, so the exclusions
    could only harden. Papering changes no exposure and buys the measurement.
    """
    runner = _runner()
    papered = runner.resolve_paper_markets(_args())
    assert "spread" in papered and "moneyline" in papered
    # Totals are the college edge and stay stakeable.
    assert "total" not in papered


def test_the_flags_still_stake_them() -> None:
    runner = _runner()
    assert "spread" not in runner.resolve_paper_markets(_args(ncaaf_spreads=True))
    assert "moneyline" not in runner.resolve_paper_markets(
        _args(ncaaf_moneylines=True))


def test_only_college_gets_this_treatment() -> None:
    runner = _runner()
    assert runner.ncaaf_side_paper(_args(league="nfl")) == frozenset()
    assert runner.ncaaf_side_paper(_args(league="mlb")) == frozenset()


def test_the_team_total_paper_posture_still_rides_along() -> None:
    runner = _runner()
    papered = runner.resolve_paper_markets(_args())
    assert {"team_total_home", "team_total_away"} <= papered


def test_a_whole_paper_league_still_papers_everything() -> None:
    runner = _runner()
    papered = runner.resolve_paper_markets(_args(league="wnba", paper=None))
    assert "__all__" in papered


def test_a_papered_market_is_priced_and_graded_rather_than_skipped() -> None:
    """The mechanical difference, at the config that decides it.

    ``exclude_markets`` short-circuits before any pricing; ``paper_markets``
    lets the bet through and zeroes the stake with a reason on the ticket.
    """
    from velocity.wagering.slate import SlateConfig

    config = SlateConfig(paper_markets=frozenset({"spread"}))
    assert config.paper_reason("spread", 0.05, 0.5) == "paper market"
    assert config.paper_reason("total", 0.05, 0.5) is None
    assert "spread" not in config.exclude_markets
