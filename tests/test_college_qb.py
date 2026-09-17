"""The college QB term: passer ids from cfbfastR, passer cells, the cell fit.

Offline: synthetic frames. The committed college plays carry
``passer_player_id`` joined from cfbfastR's per-play player stats
(scripts/attach_ncaaf_passers.py); the EPA half of the college blend can then
decompose the passer out of the offense the way the NFL fit does, on
compressed cells (docs/MODEL_LAB.md, the college QB round).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.backtest.lab import compress_plays
from velocity.features.team import fit_qb_ratings
from velocity.ingest.ncaaf import PASSER_ROLE_COLUMNS, attach_passers, passer_by_play

REPO = Path(__file__).parent.parent


def test_passer_by_play_reads_every_pass_role_and_stringifies_the_id() -> None:
    stats = pd.DataFrame({
        "play_id": ["p1", "p2", "p3", "p4", "p5", "p5"],
        "completion_player_id": [4685522.0, None, None, None, None, None],
        "incompletion_player_id": [None, 111.0, None, None, 222.0, 222.0],
        "interception_thrown_player_id": [None, None, 333.0, None, None, None],
        "sack_taken_player_id": [None, None, None, None, None, None],
        "rush_player_id": [None, None, None, 999.0, None, None],
    })
    passers = passer_by_play(stats)
    assert passers.to_dict() == {"p1": "4685522", "p2": "111", "p3": "333", "p5": "222"}
    assert passers.index.name == "play_id" and not passers.index.duplicated().any()
    assert set(PASSER_ROLE_COLUMNS) == {
        "completion_player_id", "incompletion_player_id",
        "interception_thrown_player_id", "sack_taken_player_id"}
    # A frame with none of the roles, or no rows, is an empty map.
    assert passer_by_play(stats[["play_id", "rush_player_id"]]).empty
    assert passer_by_play(stats.iloc[:0]).empty


def test_attach_passers_joins_by_play_id_and_keeps_what_was_there() -> None:
    plays = pd.DataFrame({"play_id": ["p1", "p2", "p3"], "posteam": "A"})
    passers = pd.Series(["q1", "q2"], index=pd.Index(["p1", "p2"], name="play_id"),
                        dtype="string")
    out = attach_passers(plays, passers)
    assert out["passer_player_id"].tolist()[:2] == ["q1", "q2"]
    assert pd.isna(out["passer_player_id"].iloc[2])
    assert "passer_player_id" not in plays.columns  # pure
    # A second attach with a thinner map keeps the earlier passer on the miss.
    again = attach_passers(out, passers.iloc[:1])
    assert again["passer_player_id"].tolist()[:2] == ["q1", "q2"]


def _college_plays(n_games: int = 30, per_game: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    teams = ["A", "B", "C", "D"]
    qb_effect = {"A": {"qA1": 0.15, "qA2": -0.2}, "B": {"qB": 0.05},
                 "C": {"qC": -0.05}, "D": {"qD": 0.0}}
    rows = []
    for g in range(n_games):
        home, away = rng.choice(teams, size=2, replace=False)
        week = 1 + g // 2
        for off, dfn in ((home, away), (away, home)):
            qbs = list(qb_effect[off])
            qb = qbs[0] if (off != "A" or week <= 8) else qbs[1]  # A changes starter
            for i in range(per_game):
                is_pass = i % 5 != 0
                rows.append({
                    "play_id": str(len(rows)), "game_id": f"g{g}", "season": 2025, "week": week,
                    "posteam": off, "defteam": dfn,
                    "play_type": "Pass Reception" if is_pass else "Rush",
                    "epa": (qb_effect[off][qb] if is_pass else 0.0) + rng.normal(0.0, 0.5),
                    "passer_player_id": qb if is_pass else None,
                })
    frame = pd.DataFrame(rows)
    frame["passer_player_id"] = frame["passer_player_id"].astype("string")
    return frame


def test_passer_cells_reproduce_the_play_level_qb_fit() -> None:
    plays = _college_plays()
    cells = compress_plays(plays, by_passer=True)
    assert len(cells) < len(plays)
    # A cell per team-game-passer, the no-passer plays their own cell.
    assert cells["passer_player_id"].isna().any()
    assert cells["n"].sum() == len(plays)
    full = fit_qb_ratings(plays, ridge_lambda=20.0, qb_lambda=50.0, min_dropbacks=30)
    cell = fit_qb_ratings(cells, ridge_lambda=20.0, qb_lambda=50.0, min_dropbacks=30,
                          weights=cells["n"].astype(float), count_col="n")
    for team in "ABCD":
        assert cell.offense[team] == pytest.approx(full.offense[team], abs=1e-10)
        assert cell.defense[team] == pytest.approx(full.defense[team], abs=1e-10)
    assert set(cell.qb) == set(full.qb)
    for qb in full.qb:
        assert cell.qb[qb] == pytest.approx(full.qb[qb], abs=1e-10)
    # The dropback floor counts plays, not rows: qA2 threw in a handful of
    # late games and still clears 30 dropbacks; a 200-play floor drops him.
    assert "qA2" in cell.qb
    thin = fit_qb_ratings(cells, ridge_lambda=20.0, qb_lambda=50.0, min_dropbacks=200,
                          weights=cells["n"].astype(float), count_col="n")
    assert "qA2" not in thin.qb
    # Starter detection and the pass rate agree with the play-level fit.
    assert cell.starters == full.starters and cell.starters["A"] == "qA2"
    for team in "ABCD":
        assert cell.pass_rate[team] == pytest.approx(0.8, abs=1e-9)
        assert full.pass_rate[team] == pytest.approx(0.8, abs=1e-9)
    # The QB effects come out with the right sign and the starter is priced.
    assert cell.qb["qA1"] > cell.qb["qA2"]
    assert cell.matchup_delta("A", "D", qb_id="qA1") > cell.matchup_delta("A", "D", qb_id="qA2")


def test_plain_cells_are_unchanged_by_the_flag_when_no_passer_column() -> None:
    plays = _college_plays().drop(columns=["passer_player_id"])
    assert compress_plays(plays, by_passer=True).equals(compress_plays(plays))


def test_the_college_qb_variants_ride_the_promoted_chain() -> None:
    import inspect

    from velocity.backtest.lab import ncaaf_variants

    plays = _college_plays()
    sp = pd.DataFrame({"season": [2024], "team": ["A"], "rating": [10.0],
                       "offense": [30.0], "defense": [20.0]})
    variants = ncaaf_variants(50, plays=plays, sp=sp)
    for name in ("blend-level2-sp12-scale-phase-qb150", "blend-level2-sp12-scale-phase-qb300",
                 "blend-level2-sp12-scale-phase-qb600"):
        assert name in variants
        kind, factory = variants[name]
        assert kind == "games"
        assert "predicting" in inspect.signature(factory).parameters


def test_attach_script_reports_coverage_per_season() -> None:
    _spec = importlib.util.spec_from_file_location(
        "attach_ncaaf_passers", REPO / "scripts" / "attach_ncaaf_passers.py")
    mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(mod)  # type: ignore[union-attr]
    plays = pd.DataFrame({
        "season": [2025] * 4 + [2024] * 2,
        "play_type": ["Pass Reception", "Sack", "Rush", "Pass Incompletion", "Rush", "Pass"],
        "passer_player_id": ["q", None, None, "q", None, None],
    })
    cov = mod.coverage(plays).set_index("season")
    assert cov.loc[2025, "pass_plays"] == 3 and cov.loc[2025, "with_passer"] == pytest.approx(2 / 3)
    assert cov.loc[2024, "pass_plays"] == 1 and cov.loc[2024, "with_passer"] == 0.0


# --- the EPA half's own prior and recency ---------------------------------


def test_sp_pseudo_cells_center_on_the_rated_league_and_gate_the_season() -> None:
    from velocity.ingest.ncaaf import SP_PRIOR_ANCHOR, sp_pseudo_cells

    sp = pd.DataFrame({
        "season": [2024, 2024, 2024], "team": ["A", "B", "C"], "rating": [20.0, 0.0, -20.0],
        "offense": [40.0, 30.0, 20.0], "defense": [20.0, 30.0, 40.0],
    })
    cells = sp_pseudo_cells(sp, {"A", "C", "Z"}, cutoff=pd.Timestamp("2025-02-01"),
                            k=6, league_epa=0.1, plays_per_game=50.0)
    assert list(cells.columns) == ["posteam", "defteam", "season", "week", "epa", "n"]
    assert len(cells) == 4 and set(cells["season"]) == {2025} and set(cells["week"]) == {0}
    assert (cells["n"] == 300.0).all()
    a_off = cells[(cells["posteam"] == "A")].iloc[0]
    a_def = cells[(cells["defteam"] == "A")].iloc[0]
    c_off = cells[(cells["posteam"] == "C")].iloc[0]
    assert a_off["defteam"] == SP_PRIOR_ANCHOR and a_def["posteam"] == SP_PRIOR_ANCHOR
    # +10 points a game over the rated mean at 50 plays: +0.2 EPA/play on offense;
    # the better defense allows 0.2 less than league.
    assert a_off["epa"] == pytest.approx(0.1 + 0.2)
    assert a_def["epa"] == pytest.approx(0.1 - 0.2)
    assert c_off["epa"] == pytest.approx(0.1 - 0.2)
    # Only rated teams the caller asked for; and nothing before the season closes.
    assert "Z" not in set(cells["posteam"])
    assert sp_pseudo_cells(sp, {"A"}, cutoff=pd.Timestamp("2024-12-01"), k=6).empty


def test_the_epa_prior_and_recency_variants_exist_and_fit() -> None:
    from velocity.backtest.lab import ncaaf_variants

    plays = _college_plays()
    sp = pd.DataFrame({"season": [2024] * 4, "team": list("ABCD"),
                       "rating": [10.0, 5.0, -5.0, -10.0],
                       "offense": [35.0, 30.0, 25.0, 20.0], "defense": [20.0, 25.0, 30.0, 35.0]})
    variants = ncaaf_variants(50, plays=plays, sp=sp)
    for name in ("blend-level2-sp12-scale-phase-epaprior6", "blend-level2-sp12-scale-phase-epahl34",
                 "blend-level2-sp12-scale-phase-epaprior12-epahl34"):
        assert name in variants and variants[name][0] == "games"
    games = (plays.groupby("game_id").agg(season=("season", "first"), week=("week", "first"),
                                          home_team=("posteam", "first"),
                                          away_team=("defteam", "first"))
             .reset_index())
    games["kickoff"] = pd.Timestamp("2025-08-30") + pd.to_timedelta(games["week"] * 7, unit="D")
    games["home_score"] = 28.0
    games["away_score"] = 21.0
    games["neutral_site"] = False
    # The prior variant fits with the pseudo-cells in, and the anchor never
    # reaches the projection: A (SP+ +10) prices above D (SP+ −10) at even
    # form on the field.
    _kind, factory = variants["blend-level2-sp12-scale-phase-epaprior12"]
    model = factory(games, predicting=(2025, 3))
    core = model
    while hasattr(core, "inner"):
        core = core.inner
    ratings = core.primary.ratings
    assert "__SP_PRIOR__" in ratings.teams
    assert ratings.offense["A"] > ratings.offense["D"]
    mu_home, mu_away = core.expected_points("A", "D", neutral_site=True)
    assert mu_home > mu_away
