"""College props — the substitute for a provider that has no college endpoint.

The collector bought the full NCAAF prop board every live run and the slate
could price none of it: it read FantasyPros, and FantasyPros serves no college
players at all, so the league filter came back empty and the slate skipped
while the lines were banked and never read (audit finding 6).

These pin the three things that had to be true for the bank to stand in: the
frame is the shape the sim already eats, the dispersion is college's own
rather than the NFL's, and a name that could be two players is refused instead
of guessed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from velocity.models.props_football import (
    FootballPropConfig,
    game_props,
    name_index_from_fp,
    team_player_means,
)
from velocity.models.props_ncaaf import (
    BANK_STAT_TO_FP,
    ncaaf_prop_config,
    player_prop_means,
)


def _bank(rows: list[dict]) -> pd.DataFrame:
    base = {"season": 2026, "week": 1, "game_id": "g1", "opponent": "Auburn",
            "attempts": 0.0, "pass_yards": 0.0, "pass_tds": 0.0,
            "interceptions": 0.0, "carries": 0.0, "rush_yards": 0.0,
            "rush_tds": 0.0, "targets": 0.0, "receptions": 0.0,
            "receiving_yards": 0.0, "receiving_tds": 0.0, "dk_points": 0.0}
    return pd.DataFrame([{**base, **row} for row in rows])


# --- the frame is the shape the sim already eats ------------------------------


def test_the_projection_is_the_frame_the_sim_already_eats() -> None:
    """Nothing downstream may be able to tell it did not come from the provider."""
    frame = player_prop_means(_bank([
        {"player_id": "qb1", "player_name": "A QB", "team": "Alabama",
         "position": "QB", "attempts": 30.0, "pass_yards": 300.0, "pass_tds": 2.0},
        {"player_id": "wr1", "player_name": "A WR", "team": "Alabama",
         "position": "WR", "receptions": 6.0, "receiving_yards": 90.0},
    ]))
    assert list(frame.columns) == [
        "player_id", "player_name", "team", "position", "stat", "value"]
    means = {p.name: p for p in team_player_means(frame, "Alabama")}
    assert means["A QB"].means["pass_yards"] == 300.0
    assert means["A QB"].means["pass_attempts"] == 30.0
    assert means["A WR"].means["receptions"] == 6.0
    assert means["A QB"].td_rate == 0.0  # pass_tds is the passer's market, not his rate
    assert set(name_index_from_fp(frame)) == {"aqb", "awr"}


def test_a_value_is_the_mean_of_his_recent_games() -> None:
    frame = player_prop_means(_bank([
        {"player_id": "rb1", "player_name": "A RB", "team": "Alabama",
         "position": "RB", "week": 1, "carries": 10.0, "rush_yards": 40.0},
        {"player_id": "rb1", "player_name": "A RB", "team": "Alabama",
         "position": "RB", "week": 2, "carries": 20.0, "rush_yards": 160.0},
    ]))
    by_stat = dict(zip(frame["stat"], frame["value"], strict=True))
    assert by_stat["rush_att"] == 15.0
    assert by_stat["rush_yds"] == 100.0


def test_completions_are_the_one_market_the_bank_cannot_price() -> None:
    """cfbfastR records an incompletion's passer but never a completion count.

    Ten of the eleven football prop markets are available; this is the
    eleventh, and it is left unpriced rather than inferred from a
    league-average completion rate.
    """
    assert "completions" not in BANK_STAT_TO_FP
    assert "pass_cmp" not in BANK_STAT_TO_FP.values()


def test_a_stat_he_never_accumulates_is_not_a_zero_mean() -> None:
    """A receiver with no carries must not be projected at zero rushing yards.

    Zero is a mean the sim would price an under against; absence is the
    honest statement, and the sim's own floors already skip a market a player
    has no volume in.
    """
    frame = player_prop_means(_bank([
        {"player_id": "wr1", "player_name": "A WR", "team": "Alabama",
         "position": "WR", "receptions": 5.0, "receiving_yards": 70.0},
    ]))
    assert set(frame["stat"]) == {"rec_rec", "rec_yds"}


# --- who is eligible ----------------------------------------------------------


def test_a_player_who_has_not_played_this_season_is_not_on_a_roster() -> None:
    """The window may reach back a season; eligibility may not.

    A college roster turns over every August, so six games beats one for the
    *mean* — but a player whose last snap was last year is not projected off
    a two-year-old average as though he were starting Saturday.
    """
    frame = player_prop_means(_bank([
        {"player_id": "gone", "player_name": "Graduated", "team": "Alabama",
         "position": "RB", "season": 2025, "carries": 20.0, "rush_yards": 100.0},
        {"player_id": "here", "player_name": "Still Here", "team": "Alabama",
         "position": "RB", "season": 2026, "carries": 12.0, "rush_yards": 60.0},
    ]))
    assert set(frame["player_name"]) == {"Still Here"}


def test_last_seasons_games_still_feed_a_current_players_mean() -> None:
    """The other half of that rule, or a week-1 projection reads one game."""
    frame = player_prop_means(_bank([
        {"player_id": "rb1", "player_name": "A RB", "team": "Alabama",
         "position": "RB", "season": 2025, "week": 8, "carries": 20.0},
        {"player_id": "rb1", "player_name": "A RB", "team": "Alabama",
         "position": "RB", "season": 2026, "week": 1, "carries": 10.0},
    ]))
    by_stat = dict(zip(frame["stat"], frame["value"], strict=True))
    assert by_stat["rush_att"] == 15.0  # both games, not just this season's


def test_a_name_two_players_share_is_refused_rather_than_guessed() -> None:
    """The prop line carries a name and no id, so a collision cannot resolve.

    180 names across the four banked seasons are held by more than one player
    and 20 of those are live in 2026. ``name_index_from_fp`` keeps whichever
    came first, which is a coin flip on whose line gets priced — so both are
    dropped instead, the same rule the slate already applies to a name it
    cannot resolve at all.
    """
    frame = player_prop_means(_bank([
        {"player_id": "a", "player_name": "Same Name", "team": "Alabama",
         "position": "RB", "carries": 20.0, "rush_yards": 100.0},
        {"player_id": "b", "player_name": "Same Name", "team": "Georgia",
         "position": "WR", "receptions": 5.0, "receiving_yards": 80.0},
        {"player_id": "c", "player_name": "Unique Name", "team": "Alabama",
         "position": "RB", "carries": 15.0, "rush_yards": 70.0},
    ]))
    assert set(frame["player_name"]) == {"Unique Name"}


def test_an_empty_bank_projects_nothing() -> None:
    assert player_prop_means(pd.DataFrame()).empty
    assert player_prop_means(_bank([
        {"player_id": "k", "player_name": "A Kicker", "team": "Alabama",
         "position": "K", "carries": 0.0},
    ])).empty  # no DK-rostered position, so nothing to project


def test_a_backtest_may_not_read_the_game_it_is_projecting() -> None:
    frame = player_prop_means(_bank([
        {"player_id": "rb1", "player_name": "A RB", "team": "Alabama",
         "position": "RB", "week": 1, "carries": 10.0},
        {"player_id": "rb1", "player_name": "A RB", "team": "Alabama",
         "position": "RB", "week": 2, "carries": 30.0},
    ]), before=(2026, 2))
    by_stat = dict(zip(frame["stat"], frame["value"], strict=True))
    assert by_stat["rush_att"] == 10.0  # week 2 is the game being projected


# --- the dispersion is college's own ------------------------------------------


def test_college_volume_swings_about_twice_as_hard_as_the_nfl() -> None:
    """The part that would have gone wrong quietly.

    Re-fitting on the college bank puts the team volume σ at roughly double
    the NFL's — college blowouts, tempo and talent gaps move the whole pie
    far harder. Shipping the NFL config here would simulate distributions
    about half as wide as they are, and a too-narrow distribution does not
    fail loudly: it manufactures edge on every market at once.
    """
    college, nfl = ncaaf_prop_config(), FootballPropConfig()
    assert college.pass_volume_sigma > 1.9 * nfl.pass_volume_sigma
    assert college.rush_volume_sigma > nfl.rush_volume_sigma


def test_a_position_the_college_bank_cannot_fit_falls_back_rather_than_to_zero() -> None:
    """DK lists college tight ends as receivers, so there are no TE rows.

    An unfitted position must not enter the table as 0.0 — a zero per-catch
    sd makes a player's receiving yards deterministic, which is the most
    confident a model can possibly be about something it has never seen.
    """
    college = ncaaf_prop_config()
    assert "TE" not in college.yards_sd_by_position
    assert college.per_catch_sd("TE") == college.yards_sd_per_reception > 0.0


def test_the_college_sim_is_wider_than_the_nfl_one_on_the_same_player() -> None:
    """The config difference has to reach the simulated distribution."""
    frame = player_prop_means(_bank([
        {"player_id": "wr1", "player_name": "A WR", "team": "Alabama",
         "position": "WR", "receptions": 6.0, "receiving_yards": 90.0},
    ]))
    spreads = {}
    for name, config in (("cfb", ncaaf_prop_config(n_sims=20_000)),
                         ("nfl", FootballPropConfig(n_sims=20_000))):
        sim = game_props(frame, "Alabama", "Georgia", np.random.default_rng(11), config)
        spreads[name] = float(np.std(sim.samples[("wr1", "receiving_yards")]))
    assert spreads["cfb"] > spreads["nfl"]
