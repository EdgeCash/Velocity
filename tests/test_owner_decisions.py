"""The owner's standing decisions, pinned (docs/DECISIONS.md).

Each of these encodes a choice that was made deliberately and is not to be
undone by a casual edit. They are cheap, and each one failed silently in
some earlier version of this system.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"


def _crons(name: str) -> list[str]:
    return re.findall(r'- cron: "([^"]+)"', (WORKFLOWS / name).read_text())


def _hours_by_day(crons: list[str]) -> dict[str, str]:
    return {c.split()[4]: c.split()[1] for c in crons}


# ---------------------------------------------------------------------------
# D1 — props are a core market
# ---------------------------------------------------------------------------

def test_prop_collection_tracks_every_slate_window() -> None:
    """Props ride the SAME queue delay as the slate, so they land near it.

    A fixed twice-daily pair drifted: on 2026-09-20 the 15:19 cron started at
    18:12 and the 22:51 slate found a 279-minute-old board against a
    75-minute bar, so the props surface was simply absent.
    """
    assert _hours_by_day(_crons("collect-football-props.yml")) == \
        _hours_by_day(_crons("live-slate.yml"))


def test_props_start_before_the_slate_that_reads_them() -> None:
    for cron in _crons("collect-football-props.yml"):
        assert int(cron.split()[0]) < 53, f"{cron!r} does not precede live-slate's :53"


def test_the_prop_freshness_bar_is_not_relaxed() -> None:
    """Explicitly refused in D1: a four-hour-old prop line is not a price.

    --board-max-age-min is the bar the runner prints when it declines a
    stale prop board ("bar 75min"). D1's answer to a board that keeps
    arriving late is to collect nearer the slate, never to widen this.
    """
    import argparse
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_live_slate", Path(__file__).resolve().parents[1] / "scripts" /
        "run_live_slate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser: argparse.ArgumentParser = module.build_parser()
    default = parser.get_default("board_max_age_min")
    assert default == 75.0, f"prop/board freshness bar moved to {default}"


# ---------------------------------------------------------------------------
# D2 — team totals stay
# ---------------------------------------------------------------------------

def test_team_totals_are_never_switched_off_in_a_workflow() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        assert "--no-team-totals" not in path.read_text(), path.name


def test_the_simulation_persists_each_side_s_own_score() -> None:
    """Without these kinds a team total cannot be priced off the sim."""
    import numpy as np
    from velocity.models.simulate import SimConfig, simulate_game
    from velocity.report.social import distributions_frame

    class _Proj:
        def __init__(self, sim: object) -> None:
            self.sim = sim

    sim = simulate_game(3.0, 45.0, np.random.default_rng(1), SimConfig(n_sims=2_000))
    kinds = set(distributions_frame({"g1": _Proj(sim)})["kind"])
    assert {"home_score", "away_score"} <= kinds


def test_team_totals_can_still_earn_a_curated_tier() -> None:
    """D2 requires the promotion path stay open, not that a rule exists now."""
    from velocity.wagering.plays import build_plays

    slate = pd.DataFrame([{
        "game_id": "g1", "market": "team_total_home", "side": "over",
        "point": 24.5, "book": "dk", "price": -110, "p_model": 0.60,
        "p_fair": 0.52, "edge": 0.08, "stake": 1.5, "note": None,
        "rule_tier": None, "league": "nfl",
    }])
    games = pd.DataFrame([{"game_id": "g1", "home_team": "Carolina",
                           "away_team": "Atlanta", "league": "nfl"}])
    plays = build_plays(slate, games=games)
    assert len(plays) == 1
    row = plays.iloc[0]
    assert row["tier"] in ("A+", "A", "B")   # staked and positive-edge: not Watch
    assert "Carolina" in row["selection"]


# ---------------------------------------------------------------------------
# D3 — email delivery must not be blocked by the gate
# ---------------------------------------------------------------------------

def test_the_readiness_gate_is_the_last_step() -> None:
    """A failing step stops the ones after it.

    The gate sat before the email, so a NOT READY board would have failed
    silently INTO the inbox by never arriving — the opposite of what a gate
    is for. Everything that delivers runs first; the gate only reports.
    """
    steps = yaml.safe_load((WORKFLOWS / "live-slate.yml").read_text())["jobs"]["slate"]["steps"]
    names = [s.get("name", "") for s in steps]
    assert names[-1] == "Board readiness gate", names[-3:]
    for delivering in ("Email the slate", "Upload slates (Actions artifact)"):
        assert names.index(delivering) < names.index("Board readiness gate")


def test_the_email_carries_the_whole_board_workbook() -> None:
    steps = yaml.safe_load((WORKFLOWS / "live-slate.yml").read_text())["jobs"]["slate"]["steps"]
    send = next(s for s in steps if s.get("name") == "Email the slate")
    assert "artifacts/exports/*.xlsx" in send["with"]["attachments"]


# ---------------------------------------------------------------------------
# D5 — thresholds frozen until real-world data says otherwise
# ---------------------------------------------------------------------------

def test_readiness_thresholds_are_the_agreed_ones() -> None:
    from velocity.export.readiness import (
        DEFAULT_KICKOFF_WARN_MIN,
        DEFAULT_MAX_AGE_MIN,
    )

    assert DEFAULT_MAX_AGE_MIN == 240.0
    assert DEFAULT_KICKOFF_WARN_MIN == 90.0


def test_confidence_weights_are_the_agreed_ones() -> None:
    from velocity.wagering.plays import PlaysConfig

    config = PlaysConfig()
    assert (config.edge_weight, config.rule_weight, config.intel_weight) == \
        (0.40, 0.35, 0.25)


# ---------------------------------------------------------------------------
# D6 — no synthetic ownership
# ---------------------------------------------------------------------------

def test_ownership_and_leverage_are_blank_without_a_source() -> None:
    """Blank is preferred over fabricated. No estimator may appear."""
    from velocity.export.dfs import build_dfs, build_dfs_optimizer

    pool = pd.DataFrame([
        {"player_name": "A", "position": "RB", "team": "ATL",
         "salary": 7600, "points": 17.4},
        {"player_name": "B", "position": "WR", "team": "KC",
         "salary": 6100, "points": 13.1},
    ])
    for builder in (build_dfs, build_dfs_optimizer):
        out = builder(pool)
        assert out["ownership"].isna().all()
        assert out["leverage_score"].isna().all()


def test_a_supplied_projection_is_still_honoured() -> None:
    """D6 blocks invention, not a credible source."""
    from velocity.export.dfs import build_dfs

    pool = pd.DataFrame([
        {"player_name": "A", "position": "RB", "team": "ATL",
         "salary": 7600, "points": 17.4},
        {"player_name": "B", "position": "WR", "team": "KC",
         "salary": 6100, "points": 13.1},
    ])
    out = build_dfs(pool, ownership={"A": 0.31, "B": 0.04})
    assert out["ownership"].notna().all()
    assert out["leverage_score"].notna().all()


@pytest.mark.parametrize("decision", ["D1", "D2", "D3", "D4", "D5", "D6"])
def test_every_decision_is_written_down(decision: str) -> None:
    assert f"## {decision} —" in (REPO / "docs" / "DECISIONS.md").read_text()


# ---------------------------------------------------------------------------
# Phase 13 Stage 1 — the mobile path
# ---------------------------------------------------------------------------

def test_the_email_subject_leads_with_a_bad_verdict() -> None:
    """A lock screen truncates near forty characters.

    "this board is missing something" is the one thing worth spending those
    characters on — and a READY board says nothing extra, because a prefix on
    every message is a prefix nobody reads.
    """
    from velocity.report.email_html import render_slate_email

    plays = pd.DataFrame([{
        "game_id": "g1", "market": "total", "side": "under", "point": 41.5,
        "book": "dk", "price": -110, "p_model": 0.58, "p_fair": 0.52,
        "edge": 0.06, "stake": 1.7, "note": None,
    }])

    def subject(verdict: str | None) -> str:
        return render_slate_email(plays, None, None, league="nfl",
                                  generated_at="2026-09-20T22:51:00Z",
                                  readiness=verdict)[0]

    assert subject("NOT READY").startswith("NOT READY · ")
    assert subject("DEGRADED").startswith("DEGRADED · ")
    assert subject("READY") == subject(None)
    assert not subject("READY").startswith("READY")


def test_the_email_workflow_passes_the_readiness_file() -> None:
    steps = yaml.safe_load((WORKFLOWS / "live-slate.yml").read_text())["jobs"]["slate"]["steps"]
    render = next(s for s in steps if s.get("name") == "Render slate email")
    assert "--readiness artifacts/exports/readiness.csv" in render["run"]


def test_the_dispatchable_workflows_the_shortcut_targets_exist() -> None:
    """docs/IOS_SHORTCUTS.md posts to these two by filename."""
    for name in ("live-slate.yml", "refresh-exports.yml"):
        triggers = yaml.safe_load((WORKFLOWS / name).read_text())[True]
        assert "workflow_dispatch" in triggers, name
