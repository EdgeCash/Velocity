"""The plays rebuild's context columns and the fits that condition on them.

Offline: synthetic frames only. The rebuilt ``datasets/nfl/plays.parquet``
carries the win probability, clock and score state, turnover flags, QB EPA,
CPOE and penalty markers (velocity.ingest.nfl.PBP_CONTEXT_COLUMNS); these
tests pin the normalization and the helpers the lab's play-context round
fits through (docs/MODEL_LAB.md).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.features.team import (
    attach_home_flag,
    fit_qb_ratings,
    fit_ratings,
    garbage_time_weights,
    shrink_turnover_epa,
    winsorize_epa,
)
from velocity.ingest.nfl import PBP_CONTEXT_COLUMNS, normalize_pbp
from velocity.store.schema import Plays

REPO = Path(__file__).parent.parent
FIXTURES = REPO / "tests" / "fixtures"


def _plays(n: int = 12) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    teams = np.where(np.arange(n) % 2 == 0, "A", "B")
    return pd.DataFrame({
        "play_id": [str(i) for i in range(n)],
        "game_id": ["g1"] * (n // 2) + ["g2"] * (n - n // 2),
        "season": 2025, "week": [1] * (n // 2) + [2] * (n - n // 2),
        "posteam": teams, "defteam": np.where(teams == "A", "B", "A"),
        "play_type": "pass",
        "epa": rng.normal(0.0, 1.0, n),
        "wp": np.linspace(0.0, 1.0, n),
        "interception": [1.0, 0.0] + [0.0] * (n - 2),
        "fumble_lost": [0.0] * (n - 1) + [1.0],
        "passer_player_id": np.where(teams == "A", "qbA", "qbB"),
    })


# --- normalization -------------------------------------------------------


def test_normalize_pbp_carries_the_context_columns_numeric_and_rounded() -> None:
    raw = pd.read_csv(FIXTURES / "raw_nfl_pbp.csv")
    raw = raw.assign(wp=["0.51234", "0.9", None, "bad", "0.001"], vegas_wp=0.5,
                     qtr=[1, 1, 2, 4, 4], game_seconds_remaining=3600.0,
                     score_differential=[0, 3, -7, 21, 21], interception=[0.0, 1.0, 0, 0, None],
                     fumble_lost=0.0, fumble=0.0, qb_epa=[0.123456, -1.5, 0, 0, 0],
                     cpoe=[12.3456789, None, None, None, None], penalty=0.0,
                     aborted_play=0.0)
    plays = normalize_pbp(raw)
    Plays.validate(plays)
    assert all(col in plays.columns for col in PBP_CONTEXT_COLUMNS)
    for col in PBP_CONTEXT_COLUMNS:
        assert plays[col].dtype.kind == "f", col
    wp = plays.set_index("play_id")["wp"]
    assert wp["1"] == pytest.approx(0.512)  # three decimals
    assert pd.isna(wp["3"]) and pd.isna(wp["4"])  # missing and unparsable both null
    assert plays["qb_epa"].iloc[0] == pytest.approx(0.123)
    assert plays["cpoe"].iloc[0] == pytest.approx(12.346)
    assert plays["interception"].iloc[1] == 1.0 and pd.isna(plays["interception"].iloc[4])


def test_normalize_pbp_without_the_context_columns_writes_nulls() -> None:
    """The refresh normalizes through the same list, so an older extract
    (or a fixture) still validates — the context is simply absent."""
    plays = normalize_pbp(pd.read_csv(FIXTURES / "raw_nfl_pbp.csv"))
    for col in PBP_CONTEXT_COLUMNS:
        assert plays[col].isna().all(), col


def test_build_script_distills_the_plays_through_the_canonical_path() -> None:
    _spec = importlib.util.spec_from_file_location(
        "build_nfl_pbp_datasets", REPO / "scripts" / "build_nfl_pbp_datasets.py")
    mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert set(PBP_CONTEXT_COLUMNS) <= set(mod.PLAY_COLS)
    raw = pd.read_csv(FIXTURES / "raw_nfl_pbp.csv").assign(wp=0.5)
    plays = mod.distill_plays(raw)
    # The timeout row (no posteam / epa) is dropped, ids are strings, the
    # context rides along — exactly what the refresh's top-up writes.
    assert len(plays) == 4 and plays["posteam"].notna().all()
    assert plays["play_id"].map(type).eq(str).all()
    assert (plays["wp"] == 0.5).all()


# --- the helpers ---------------------------------------------------------


def test_garbage_time_weights_hit_the_decided_plays_only() -> None:
    plays = _plays()
    w = garbage_time_weights(plays, factor=0.25, band=0.05)
    assert list(w.index) == list(plays.index)
    decided = (plays["wp"] <= 0.05) | (plays["wp"] >= 0.95)
    assert (w[decided] == 0.25).all() and (w[~decided] == 1.0).all()
    assert decided.sum() == 2  # the two ends of the linspace
    # No probability column, or a null probability: nothing is touched.
    assert (garbage_time_weights(plays.drop(columns=["wp"])) == 1.0).all()
    nulls = plays.assign(wp=np.nan)
    assert (garbage_time_weights(nulls, factor=0.0) == 1.0).all()
    # The Vegas-anchored column is selectable, and the band widens.
    v = garbage_time_weights(plays.rename(columns={"wp": "vegas_wp"}),
                             wp_col="vegas_wp", factor=0.5, band=0.10)
    assert (v == 0.5).sum() == 4
    with pytest.raises(ValueError):
        garbage_time_weights(plays, factor=1.5)


def test_shrink_turnover_epa_scales_interceptions_and_lost_fumbles_only() -> None:
    plays = _plays()
    out = shrink_turnover_epa(plays, 0.5)
    turnover = plays["interception"].eq(1.0) | plays["fumble_lost"].eq(1.0)
    assert turnover.sum() == 2
    assert np.allclose(out.loc[turnover, "epa"], 0.5 * plays.loc[turnover, "epa"])
    assert np.allclose(out.loc[~turnover, "epa"], plays.loc[~turnover, "epa"])
    assert "epa" in plays.columns and np.allclose(plays["epa"], _plays()["epa"])  # pure
    # Without the flags the frame comes back untouched.
    bare = plays.drop(columns=["interception", "fumble_lost"])
    assert shrink_turnover_epa(bare, 0.5) is bare
    with pytest.raises(ValueError):
        shrink_turnover_epa(plays, -1.0)


def test_winsorize_epa_clips_both_tails() -> None:
    plays = _plays().assign(epa=[-9.0, 9.0, 0.5, -0.5] + [0.0] * 8)
    out = winsorize_epa(plays, 3.0)
    assert out["epa"].tolist()[:4] == [-3.0, 3.0, 0.5, -0.5]
    assert plays["epa"].iloc[0] == -9.0  # pure
    with pytest.raises(ValueError):
        winsorize_epa(plays, 0.0)


def test_attach_home_flag_reads_the_games_frame() -> None:
    plays = _plays()
    games = pd.DataFrame({
        "game_id": ["g1", "g2", "g3"], "home_team": ["A", "B", "A"],
        "away_team": ["B", "A", "B"], "neutral_site": [False, True, False],
    })
    out = attach_home_flag(plays, games)
    g1 = out[out["game_id"] == "g1"]
    assert (g1.loc[g1["posteam"] == "A", "home"] == 0.5).all()
    assert (g1.loc[g1["posteam"] == "B", "home"] == -0.5).all()
    assert (out.loc[out["game_id"] == "g2", "home"] == 0.0).all()  # neutral
    # A play whose game is unknown, and a frame without neutral_site.
    orphan = attach_home_flag(plays.assign(game_id="zz"), games)
    assert (orphan["home"] == 0.0).all()
    plain = attach_home_flag(plays, games.drop(columns=["neutral_site"]))
    assert (plain.loc[plain["game_id"] == "g2", "home"].abs() == 0.5).all()
    assert "home" not in plays.columns  # pure


# --- the home column in the QB fit ---------------------------------------


def _home_edge_plays(edge: float, n_games: int = 40, per_game: int = 30) -> pd.DataFrame:
    """Two even teams, alternating hosts, a true home edge of ``edge`` EPA/play."""
    rng = np.random.default_rng(11)
    rows = []
    for g in range(n_games):
        home, away = ("A", "B") if g % 2 == 0 else ("B", "A")
        for off, dfn, flag in ((home, away, 0.5), (away, home, -0.5)):
            for _ in range(per_game):
                rows.append({
                    "play_id": str(len(rows)), "game_id": f"g{g}", "season": 2025,
                    "week": 1 + g // 2, "posteam": off, "defteam": dfn, "play_type": "pass",
                    "epa": edge * flag + rng.normal(0.0, 0.2), "home": flag,
                    "passer_player_id": f"qb{off}",
                })
    return pd.DataFrame(rows)


def _schedule_for(plays: pd.DataFrame) -> pd.DataFrame:
    """The games frame behind ``_home_edge_plays`` — hosts from the flag, a
    weekly kickoff, scores the level calibration can fit on."""
    rows = []
    for game_id, group in plays.groupby("game_id", sort=False):
        host = group.loc[group["home"] > 0, "posteam"].iloc[0]
        visitor = group.loc[group["home"] < 0, "posteam"].iloc[0]
        week = int(group["week"].iloc[0])
        rows.append({
            "game_id": game_id, "season": 2025, "week": week, "home_team": host,
            "away_team": visitor, "kickoff": pd.Timestamp("2025-09-07") + pd.Timedelta(weeks=week),
            "home_score": 24.0, "away_score": 21.0, "neutral_site": False, "roof": "outdoors",
            "home_qb_id": f"qb{host}", "away_qb_id": f"qb{visitor}",
        })
    return pd.DataFrame(rows)


def test_qb_fit_recovers_the_home_edge_and_stays_home_neutral() -> None:
    plays = _home_edge_plays(0.12)
    with_home = fit_qb_ratings(plays, ridge_lambda=1.0, qb_lambda=1.0, home_col="home")
    without = fit_qb_ratings(plays, ridge_lambda=1.0, qb_lambda=1.0)
    assert with_home.home_epa == pytest.approx(0.12, abs=0.03)
    assert without.home_epa == 0.0
    # The same edge the team fit sees, so the two fits agree on what home is.
    team = fit_ratings(plays, ridge_lambda=1.0, home_col="home")
    assert team.home_epa == pytest.approx(with_home.home_epa, abs=1e-6)
    # Two even teams on a balanced schedule: no team edge either way.
    assert abs(with_home.matchup_delta("A", "B")) < 0.03
    # A missing column is the plain fit.
    assert fit_qb_ratings(plays, home_col="absent").home_epa == 0.0


def test_the_play_context_variants_run_the_whole_promoted_chain() -> None:
    """Each candidate wraps the promoted chain (scale in it), so it must
    declare the predicting hint the engine looks for, and the home variant
    must price the fitted edge in place of the constant."""
    import inspect

    from velocity.backtest.lab import nfl_variants

    schedule = pd.DataFrame({
        "game_id": ["g"], "season": [2025], "week": [1], "home_team": ["A"], "away_team": ["B"],
        "kickoff": [pd.Timestamp("2025-09-07")], "home_score": [1.0], "away_score": [0.0],
        "neutral_site": [False], "roof": ["outdoors"], "home_qb_id": ["x"], "away_qb_id": ["y"],
    })
    variants = nfl_variants(50, schedule=schedule)
    names = [n for n in variants if n.startswith("live-nfl-promoted-")
             and not n.startswith("live-nfl-promoted-cold")]
    if not names:  # the weather archive is what enables the promoted chain
        pytest.skip("no weather archive")
    assert {"live-nfl-promoted-gt0.5", "live-nfl-promoted-to0.5",
            "live-nfl-promoted-win4", "live-nfl-promoted-home"} <= set(names)
    for name in names:
        _kind, factory = variants[name]
        assert "predicting" in inspect.signature(factory).parameters, name
    # Through the whole chain on a schedule that carries the plays' games,
    # so the level calibration inside it has scores to fit.
    plays = _home_edge_plays(0.10).assign(wp=0.5, interception=0.0, fumble_lost=0.0)
    _kind, home_factory = nfl_variants(50, schedule=_schedule_for(plays))[
        "live-nfl-promoted-home"]
    model = home_factory(plays, predicting=(2025, 30))
    core = model
    while hasattr(core, "inner"):
        core = core.inner
    assert core.config.hfa_points == pytest.approx(
        core.config.plays_per_game * core.ratings.home_epa)
    assert core.ratings.home_epa == pytest.approx(0.10, abs=0.03)
