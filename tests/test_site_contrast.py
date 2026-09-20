"""The hub's palette stays readable — the gate, run in CI.

The park palette (docs/SITE.md, "the park re-skin") takes Ballpark Pal's hues
and darkens them, because their own pairs sit under the AA bar and this
surface's type is a quarter the size theirs is. That darkening is the whole
reason the re-skin is legible, and it is exactly the kind of thing a later
"the green looks muddy, brighten it" undoes by accident. So it is a test.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LAYOUT = REPO / "site" / "pages" / "+layout.svelte"

_spec = importlib.util.spec_from_file_location(
    "check_site_contrast", REPO / "scripts" / "check_site_contrast.py")
assert _spec is not None and _spec.loader is not None
_CONTRAST = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_CONTRAST)


@pytest.fixture(scope="module")
def tokens() -> dict[str, str]:
    return _CONTRAST.read_tokens(LAYOUT)


def test_every_token_pair_clears_the_aa_bar(tokens: dict[str, str]) -> None:
    """Each ink on each surface, each fill under its ink, each pill on its tint."""
    bad = _CONTRAST.failures(tokens)
    assert not bad, "palette pairs under 4.5:1:\n  " + "\n  ".join(bad)


def test_the_contrast_maths_is_right() -> None:
    """Anchors from the WCAG definition, so a broken helper cannot pass the gate."""
    assert _CONTRAST.contrast("#ffffff", "#000000") == pytest.approx(21.0)
    assert _CONTRAST.contrast("#ffffff", "#ffffff") == pytest.approx(1.0)
    # The pair that started this: Ballpark Pal's own cream on their own dirt.
    assert _CONTRAST.contrast("#DFDFD4", "#846954") == pytest.approx(3.79, abs=0.01)
    # Compositing a tint is a real blend, not a guess at one.
    assert _CONTRAST.composite("#000000", 0.5, "#ffffff") == "#808080"
    assert _CONTRAST.composite("#123456", 1.0, "#ffffff") == "#123456"


def test_the_grass_fill_is_never_used_as_type() -> None:
    """``--v-grass`` is bright enough to read as Ballpark Pal and too bright to set type in.

    It is declared fill-only and carries ``--v-ink`` on top. A ``color:`` rule
    pointing at it would pass the pair gate above (which never checks it as an
    ink) and be unreadable on the page, so the restriction is checked here.
    """
    offenders = []
    for path in sorted((REPO / "site" / "components").rglob("*.svelte")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            inks = ("color:", "-webkit-text-fill-color:")
            if "var(--v-grass)" in stripped and stripped.startswith(inks):
                offenders.append(f"{path.relative_to(REPO)}:{n}: {stripped}")
    assert not offenders, "--v-grass is a fill, not an ink:\n  " + "\n  ".join(offenders)


def test_hairlines_are_dark_on_the_light_ground(tokens: dict[str, str]) -> None:
    """White-alpha hairlines are invisible on bone; every one of them had to flip.

    This is the single change most likely to be reverted by muscle memory when
    someone copies a rule in from the dark board's history.
    """
    for key in ("v-line", "v-line-2"):
        assert tokens[key].startswith("rgba("), f"{key} should stay a composited alpha"
        r, g, b = (int(part) for part in tokens[key][5:].split(",")[:3])
        assert max(r, g, b) < 128, f"{key} is a light hairline on a light ground: {tokens[key]}"
