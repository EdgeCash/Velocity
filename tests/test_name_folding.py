"""One way to match a player name — and the twenty-one hitters that cost.

Four modules had grown their own normalizer, and all four stripped case and
punctuation while leaving **diacritics** alone. Providers do not agree on
those: BettingPros serves "Jose Ramirez", MLB statsapi banks "José Ramírez",
and the two never matched.

Measured on one live board (2026-09-16, 321 distinct MLB prop players): the old
normalizer resolved 267 against the batter bank, folding resolves 288, and
against the full bank (batters *and* pitchers) folding takes it from 83.2% to
99.7%. The names it lost were José Ramírez, Julio Rodríguez, Eugenio Suárez,
Jeremy Peña and seventeen others — their props abstained entirely, because an
unresolved player is skipped without a word.
"""

from __future__ import annotations

import pytest
from velocity.util.names import fold_name, fold_name_words

JOSE = "José Ramírez"
PENA = "Jeremy Peña"
ACUNA = "Luisangel Acuña Jr."


@pytest.mark.parametrize(
    ("provider", "banked"),
    [
        ("Jose Ramirez", JOSE),
        ("Jeremy Pena", PENA),
        ("Julio Rodriguez", "Julio Rodríguez"),
        ("Eugenio Suarez", "Eugenio Suárez"),
        ("Luisangel Acuna", ACUNA),
        ("Cedric Mullins", "Cedric Mullins II"),
        ("Ronald Acuna Jr", "Ronald Acuña Jr."),
    ],
)
def test_the_two_spellings_of_a_player_agree(provider: str, banked: str) -> None:
    assert fold_name(provider) == fold_name(banked)


def test_folding_does_not_merge_different_players() -> None:
    """A fuzzy matcher that guesses wrong pairs two players, which is worse.

    The fold is deliberately conservative — accents, suffixes, punctuation —
    and makes no attempt at nicknames or initials.
    """
    distinct = ["Jose Ramirez", "Jose Ramos", "J Ramirez", "Jose Alberto Ramirez"]
    assert len({fold_name(n) for n in distinct}) == len(distinct)


def test_an_absent_name_folds_to_empty_rather_than_a_key() -> None:
    """``""`` must read as "no match", never as a key two nameless rows share."""
    assert fold_name(None) == ""
    assert fold_name("") == ""
    assert fold_name("   ") == ""


def test_the_word_preserving_fold_keeps_boundaries() -> None:
    """The intel matcher falls back to last name + first initial.

    Folding to a single token silently disables that fallback — and a fallback
    that never fires is a veto that never fires.
    """
    assert fold_name_words(JOSE) == "jose ramirez"
    assert len(fold_name_words(JOSE).split()) == 2
    assert fold_name_words(JOSE) == fold_name_words("Jose Ramirez")


def test_every_resolver_folds_the_same_way() -> None:
    """Four modules, one behaviour — the point of the shared helper.

    Asserted through each module's own entry point rather than by reading the
    source, so a future local re-implementation that drifts still fails here.
    """
    from velocity.backtest.props_football import _normalize_name as backtest_fold
    from velocity.intel.signals import _name_key
    from velocity.models.props_football import _normalize_name as model_fold
    from velocity.wagering.props_slate import _normalize as slate_fold

    for folder in (slate_fold, model_fold, backtest_fold):
        assert folder("Jose Ramirez") == folder(JOSE), folder
    assert _name_key("Jose Ramirez") == _name_key(JOSE)


def test_the_prop_resolver_finds_an_accented_bank_entry() -> None:
    """The live defect, at the function that caused it."""
    import pandas as pd
    from velocity.wagering.props_slate import build_name_index, resolve_player

    bank = pd.DataFrame({"player_id": ["660670"], "player_name": [JOSE]})
    index = build_name_index(bank)
    assert resolve_player("Jose Ramirez", index) == "660670"


def test_the_availability_veto_matches_across_spellings() -> None:
    """A missed match here is a missed veto — the worst direction to fail."""
    from velocity.intel.context import InjuryOut
    from velocity.intel.signals import _match_out

    report = (InjuryOut(player_name=JOSE, position="3B", status="out"),)
    assert _match_out("Jose Ramirez", report) is not None
    # And the last-name + initial fallback still fires.
    initialed = (InjuryOut(player_name="J. Ramirez", position="3B", status="out"),)
    assert _match_out(JOSE, initialed) is not None
