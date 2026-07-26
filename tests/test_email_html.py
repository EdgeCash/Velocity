"""Slate email rendering — subject counts, matchup labeling, and the heartbeat.

The email is the delivery surface the operator actually reads, so these pin the
things that make it trustworthy at a glance: the subject line carries the play
counts, opaque provider game ids become "Away @ Home" matchups, every section
renders (with an honest empty note when nothing qualified), and the same-game
parlay caveat appears exactly when parlays do.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
from velocity.report.email_html import render_slate_email

REPO = Path(__file__).parent.parent


def _plays() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_id": ["evt1", "evt1"],
            "market": ["total_i1", "moneyline_f5"],
            "side": ["under", "home"],
            "point": [0.5, None],
            "book": ["dk", "dk"],
            "price": [-110, -120],
            "p_model": [0.75, 0.6667],
            "p_fair": [0.5, 0.5217],
            "edge": [0.25, 0.145],
            "stake": [5.0, 3.2],
            "league": ["mlb", "mlb"],
            "generated_at": [pd.Timestamp("2026-07-26 14:00")] * 2,
        }
    )


def _games_map() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_id": ["evt1"],
            "home_team": ["Los Angeles Dodgers"],
            "away_team": ["San Francisco Giants"],
            "kickoff": [pd.Timestamp("2026-07-26 23:10")],
        }
    )


def _parlays() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "legs": ["SF@LAD moneyline home (+100) + NYY@BOS total over 8.5 (-110)"],
            "n_legs": [2],
            "price": [300],
            "decimal": [4.0],
            "p_win": [0.3],
            "ev": [0.2],
            "same_game": [False],
            "stake": [1.0],
        }
    )


def test_subject_counts_the_plays() -> None:
    subject, _ = render_slate_email(
        _plays(), None, _parlays(),
        games_map=_games_map(), generated_at="2026-07-26 14:00",
    )
    assert subject == "Velocity MLB: 2 plays, 0 props, 1 parlay (Jul 26)"


def test_empty_slate_is_a_heartbeat() -> None:
    subject, html = render_slate_email(None, None, None)
    assert subject == "Velocity MLB: no plays today"
    assert "No game bet cleared" in html
    assert "No prop cleared" in html
    assert "No parlay cleared" in html


def test_matchup_replaces_the_opaque_game_id() -> None:
    _, html = render_slate_email(_plays(), None, None, games_map=_games_map())
    assert "San Francisco Giants @ Los Angeles Dodgers" in html
    assert "evt1" not in html  # the provider id never reaches the reader


def test_parlay_caveat_only_with_parlays() -> None:
    _, with_parlays = render_slate_email(None, None, _parlays())
    _, without = render_slate_email(None, None, None)
    assert "books reprice correlated SGPs" in with_parlays
    assert "books reprice correlated SGPs" not in without


def test_styles_are_inline() -> None:
    # Email clients strip <style> blocks; every cell must carry its own style.
    _, html = render_slate_email(_plays(), None, None, games_map=_games_map())
    assert "<style" not in html
    assert '<td style="' in html
    assert '<th style="' in html


def test_render_script_writes_body_and_subject(tmp_path: Path) -> None:
    slate = tmp_path / "slate"
    slate.mkdir()
    stamp = "20260726T140000Z"
    _plays().to_parquet(slate / f"slate_mlb_{stamp}.parquet", index=False)
    _games_map().to_parquet(slate / f"games_mlb_{stamp}.parquet", index=False)
    _parlays().to_parquet(slate / f"slate_mlb_parlays_{stamp}.parquet", index=False)
    out = tmp_path / "email"
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "render_slate_email.py"),
         "--slate-dir", str(slate), "--out-dir", str(out), "--league", "mlb"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    subject = (out / "subject.txt").read_text()
    assert subject == "Velocity MLB: 2 plays, 0 props, 1 parlay (Jul 26)"
    html = (out / "slate_email.html").read_text()
    assert "San Francisco Giants @ Los Angeles Dodgers" in html
    assert "SF@LAD moneyline home" in html


def test_render_script_survives_an_empty_folder(tmp_path: Path) -> None:
    slate = tmp_path / "slate"
    slate.mkdir()  # off-day: the runner wrote nothing at all
    out = tmp_path / "email"
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "render_slate_email.py"),
         "--slate-dir", str(slate), "--out-dir", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert (out / "subject.txt").read_text() == "Velocity MLB: no plays today"
