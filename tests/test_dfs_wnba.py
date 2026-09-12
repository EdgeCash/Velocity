"""DraftKings' WNBA roster spec, read off DK's own rules API.

Captured 2026-09-12 with no WNBA board running anywhere on the lobby: the
roster template is public whether or not a slate is live, so the spec below
is DK's rather than a reconstruction. What is still missing is the half that
cannot be read — the scoring constants have no JSON endpoint, and this repo
banks no WNBA player rates for a scorer to price.
"""

from __future__ import annotations


def test_the_wnba_roster_is_dks_own_six_player_shape() -> None:
    """Game type 37, read with no WNBA board running.

    The template is public whether or not a slate is, so this is DK's spec
    rather than a reconstruction: two guards, three forwards, and a utility
    slot either can fill, under the same $50,000 cap as every other classic.
    """
    from velocity.dfs.optimizer import SALARY_CAP, WNBA_CLASSIC

    assert WNBA_CLASSIC.slots == ("G", "G", "F", "F", "F", "UTIL")
    assert WNBA_CLASSIC.base_counts == {"G": 2, "F": 3}
    assert WNBA_CLASSIC.flex == (("UTIL", ("G", "F")),)
    assert len(WNBA_CLASSIC.slots) == 6
    assert SALARY_CAP == 50_000


def test_the_wnba_spec_is_not_priceable_yet_and_does_not_pretend_to_be() -> None:
    # A spec with no scorer behind it would build a lineup out of nothing;
    # this repo banks WNBA team box scores and no player rates at all.
    from velocity.dfs.pipeline import LEAGUE_SPECS

    assert "wnba" not in LEAGUE_SPECS
