"""Unit splits — the phase classification and the opponent adjustment.

The ratings fit says how good a team is; this says the SHAPE the fit hides,
which is what a matchup surface reads (docs/FOOTBALL_PAL.md). Two things have
to be right: a college play label has to land in the right phase, and the
opponent adjustment has to move a team in the direction its schedule earned.
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.features.units import UNIT_COLUMNS, classify_phase, unit_splits


def test_the_nfl_labels_map_straight_across() -> None:
    kinds = pd.Series(["pass", "run", "punt", "qb_kneel", None])
    phases = classify_phase(kinds, "nfl")
    assert list(phases[:2]) == ["pass", "rush"]
    # Anything that is not a snap has no phase rather than a guessed one.
    assert phases[2:].isna().all()


@pytest.mark.parametrize(
    ("label", "phase"),
    [
        ("rush", "rush"),
        ("rushing touchdown", "rush"),
        ("pass reception", "pass"),
        ("pass incompletion", "pass"),
        ("passing touchdown", "pass"),
        # A sack is a dropback that lost yards, and an interception is a pass
        # that went to the wrong team; both are pass plays.
        ("sack", "pass"),
        ("interception", "pass"),
        ("pass interception return", "pass"),
        ("interception return touchdown", "pass"),
    ],
)
def test_the_college_prose_lands_in_the_right_phase(label: str, phase: str) -> None:
    assert classify_phase(pd.Series([label]), "ncaaf").iloc[0] == phase


@pytest.mark.parametrize(
    "label", ["fumble recovery (own)", "safety", "fumble return touchdown"])
def test_a_play_that_is_neither_is_dropped_rather_than_assigned(label: str) -> None:
    assert classify_phase(pd.Series([label]), "ncaaf").isna().all()


def _schedule() -> pd.DataFrame:
    """A board built so the adjustment's answer is computable by hand.

    A and B both average exactly 0.0 EPA per pass. A faced only a good pass
    defense, B only a bad one, and C supplies the plays that make those two
    defenses good and bad. With the league's pass mean at 0.0, A must come
    out at +0.5 and B at −0.5.
    """
    rows = []

    def drive(offense: str, defense: str, epa: float, n: int = 30) -> None:
        rows.extend(
            {"season": 2026, "posteam": offense, "defteam": defense,
             "play_type": "pass", "epa": epa, "success": epa > 0}
            for _ in range(n)
        )

    drive("A", "D_good", 0.0)
    drive("B", "D_bad", 0.0)
    drive("C", "D_good", -1.0)
    drive("C", "D_bad", 1.0)
    return pd.DataFrame(rows)


def test_the_adjustment_credits_the_schedule_a_team_actually_played() -> None:
    splits = unit_splits(_schedule(), "nfl").set_index(["team", "side", "phase"])
    a = splits.loc[("A", "offense", "pass")]
    b = splits.loc[("B", "offense", "pass")]
    # Identical raw numbers...
    assert a["epa_per_play"] == pytest.approx(0.0)
    assert b["epa_per_play"] == pytest.approx(0.0)
    # ...and opposite adjusted ones, because the schedules were opposite.
    assert a["epa_adjusted"] == pytest.approx(0.5)
    assert b["epa_adjusted"] == pytest.approx(-0.5)


def test_the_adjustment_uses_the_opponents_season_not_that_one_game() -> None:
    """The correction reads each opponent's season-long unit, as it should.

    D_good allowed (30 × 0.0 + 30 × −1.0) / 60 = −0.5, and it earned that
    against A and C. It is tempting to read the correction off what those
    offenses did IN THOSE GAMES (0.0 and −1.0), but the right input is what
    the offense is over the season: A and C both average exactly 0.0, which
    is the league's pass mean, so D_good played a dead-average schedule and
    the adjustment moves it by nothing. A team cannot be credited for
    holding an offense down and separately credited for the offense being
    bad — that is the same evidence counted twice.
    """
    splits = unit_splits(_schedule(), "nfl").set_index(["team", "side", "phase"])
    good = splits.loc[("D_good", "defense", "pass")]
    assert good["epa_per_play"] == pytest.approx(-0.5)
    assert good["epa_adjusted"] == pytest.approx(-0.5)
    # C is the control: it faced one good defense and one bad one, so its
    # schedule nets out and its adjusted number equals its raw one.
    c = splits.loc[("C", "offense", "pass")]
    assert c["epa_per_play"] == pytest.approx(0.0)
    assert c["epa_adjusted"] == pytest.approx(0.0)


def test_thin_cells_are_dropped_and_the_survivors_carry_their_count() -> None:
    frame = _schedule()
    thin = pd.DataFrame([
        {"season": 2026, "posteam": "Z", "defteam": "A", "play_type": "run",
         "epa": 3.0, "success": True},
    ])
    splits = unit_splits(pd.concat([frame, thin], ignore_index=True), "nfl",
                         min_plays=25)
    assert "Z" not in set(splits["team"])
    assert (splits["plays"] >= 25).all()


def test_a_named_season_pins_the_window_to_it() -> None:
    older = _schedule().assign(season=2025, epa=5.0)
    frame = pd.concat([older, _schedule()], ignore_index=True)
    pinned = unit_splits(frame, "nfl", season=2025)
    assert (pinned["season_from"] == 2025).all()
    assert (pinned["season_to"] == 2025).all()
    # Pinned means pinned: last season's plays only, not both averaged.
    assert pinned.set_index(["team", "side", "phase"]).loc[
        ("A", "offense", "pass"), "plays"] == 30


def test_a_frame_that_cannot_count_games_reads_the_newest_season_alone() -> None:
    """Without game_id there is no way to know whether a season is thin, and
    guessing wrong silently blends a season nobody asked for."""
    older = _schedule().assign(season=2025, epa=5.0)
    frame = pd.concat([older, _schedule()], ignore_index=True)
    out = unit_splits(frame, "nfl")
    assert (out["season_from"] == 2026).all()
    assert (out["season_to"] == 2026).all()


def test_a_thin_season_widens_the_window_and_the_rows_say_so() -> None:
    """One game played is not a unit.

    With a single game each, a team's split IS its only opponent's mirror,
    and the opponent adjustment collapses every such team to exactly the
    league average — correct arithmetic, no information. The window reaches
    back a season rather than publish that, and every row carries the window
    it used so the reader is never guessing which year they are looking at.
    """
    def season(year: int, tag: str) -> pd.DataFrame:
        frame = _schedule()
        return frame.assign(season=year,
                            game_id=[f"{tag}{i // 30}" for i in range(len(frame))])

    thin = season(2026, "new")           # one game per team
    frame = pd.concat([season(2025, "old"), thin], ignore_index=True)

    widened = unit_splits(frame, "nfl", min_games=4)
    assert (widened["season_from"] == 2025).all()
    assert (widened["season_to"] == 2026).all()

    # ...and a season with enough games is left alone.
    fat = pd.concat(
        [season(2026, f"g{n}").assign(game_id=lambda d, n=n: d["game_id"] + f"-{n}")
         for n in range(5)], ignore_index=True)
    kept = unit_splits(pd.concat([season(2025, "old"), fat], ignore_index=True),
                       "nfl", min_games=4)
    assert (kept["season_from"] == 2026).all()
    assert (kept["games"] >= 4).all()


def test_an_empty_or_shapeless_frame_gives_a_typed_empty_table() -> None:
    assert list(unit_splits(pd.DataFrame(), "nfl").columns) == UNIT_COLUMNS
    assert unit_splits(pd.DataFrame({"epa": [1.0]}), "nfl").empty
    # Nothing classifiable at all is empty, not a crash.
    unlabelled = pd.DataFrame([{"season": 2026, "posteam": "A", "defteam": "B",
                                "play_type": "punt", "epa": 0.1, "success": False}])
    assert unit_splits(unlabelled, "nfl").empty
