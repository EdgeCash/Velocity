"""One way to match a player name across providers.

Four modules had grown their own normalizer — the prop slate's resolver, the
intel layer's veto matcher, and the football prop model's on both the live and
backtest sides — and all four stripped punctuation and case while leaving
**diacritics alone**. Providers do not agree on those: BettingPros serves
"Jose Ramirez", MLB statsapi banks "José Ramírez", and the two never matched.

Measured on one live board (2026-09-16, 321 distinct MLB prop players): the old
normalizer resolved 267, folding resolves 288. The twenty-one it lost were not
marginal call-ups — they were José Ramírez, Julio Rodríguez, Eugenio Suárez,
Jeremy Peña and a dozen others, whose props abstained entirely and silently
because an unresolved player is simply skipped.

That is the failure mode this repo keeps meeting: no error, no warning, just a
board quietly missing its best-known names.

``fold_name`` is deliberately conservative. It folds accents, drops generational
suffixes and strips everything that is not a letter or digit. It does NOT try
to be clever about nicknames, initials or hyphenation — a fuzzy matcher that
guesses wrong pairs two different players, which is worse than abstaining.
"""

from __future__ import annotations

import re
import unicodedata

# Generational suffixes, matched as whole words after case folding. "Jr"/"II"
# travel inconsistently between feeds and never distinguish two players on the
# same board.
_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def fold_name(name: object) -> str:
    """A provider-agnostic match key for a player name.

    Accents are decomposed and their combining marks dropped, so "José" and
    "Jose" agree; generational suffixes go; then everything that is not a
    letter or digit is removed. An empty or missing name folds to ``""``,
    which callers should treat as "no match" rather than as a key.
    """
    if name is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(name))
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    without_suffix = _SUFFIXES.sub(" ", without_accents.casefold())
    return re.sub(r"[^a-z0-9]+", "", without_suffix)


def fold_name_words(name: object) -> str:
    """:func:`fold_name`, but keeping single spaces between words.

    The intel layer's matcher falls back to last name plus first initial when
    an exact match fails, which needs the word boundary — folding to one token
    silently disables that fallback, and a fallback that never fires is a veto
    that never fires.
    """
    if name is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(name))
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    without_suffix = _SUFFIXES.sub(" ", without_accents.casefold())
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", without_suffix).split())
