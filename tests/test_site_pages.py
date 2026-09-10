"""Evidence page hygiene — the traps that break the site build silently.

The site is built by `npm run build`, which CI does not run (the gate is
Python). These are the cheap checks that catch the two failure modes that
actually bit, plus the frontmatter the navigation depends on, so a bad page
fails in pytest rather than in the nightly deploy.

The traps, both real:

1. **Underscores inside a prose interpolation.** Evidence runs its markdown
   pass over the page *before* Svelte compiles it, and a pair of underscores
   anywhere in a paragraph becomes ``<em>`` — including inside a ``{...}``
   expression, and including across two lines. ``{bank[0]?.mode_note}`` two
   lines apart from another ``mode_note`` compiled to
   ``{bank[0]?.mode<em>note}`` and the build died with ``Expected }``.
2. **Quote literals inside a prose interpolation.** The same pass smart-quotes
   ``''`` into typographic quotes, so ``{x ?? ''}`` compiles to invalid JS and
   the build dies with ``Unexpected character '”'``.

Component *attributes* are not prose and are unaffected; only text lines are
checked.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PAGES = Path(__file__).resolve().parents[1] / "site" / "pages"
MD_PAGES = sorted(PAGES.rglob("*.md"))

# A ```sql ...``` fence is not prose, and neither is a <Component ... /> tag.
_FENCE = re.compile(r"^```")
_TAG_LINE = re.compile(r"^\s*(<[A-Za-z]|/?>|[a-zA-Z-]+=)")
_INTERP = re.compile(r"\{[^{}]*\}")
_BLOCK_TAG = re.compile(r"\{[#/:][^{}]*\}")


def prose_interpolations(text: str) -> list[tuple[int, str]]:
    """Every ``{...}`` that sits in a markdown paragraph, with its line number."""
    found: list[tuple[int, str]] = []
    in_fence = False
    in_tag = False
    previous = ""
    for n, raw in enumerate(text.splitlines(), start=1):
        if _FENCE.match(raw):
            in_fence = not in_fence
            previous = raw
            continue
        if in_fence:
            continue
        # A multi-line component tag: skip until it closes.
        if in_tag:
            if raw.rstrip().endswith(("/>", ">")):
                in_tag = False
            continue
        if raw.lstrip().startswith("<") and not raw.rstrip().endswith(("/>", ">")):
            in_tag = True
            continue
        if _TAG_LINE.match(raw):
            continue
        # A Svelte block tag alone on its line is block-level and safe — as
        # long as a blank line separates it from the paragraph above. Without
        # that blank line markdown absorbs it into the paragraph and
        # emphasises straight through the expression, which is exactly how
        # `{#if bank[0]?.mode_note}` became `{#if bank[0]?.mode<em>note}`.
        stripped = raw.strip()
        if _BLOCK_TAG.fullmatch(stripped):
            if previous.strip() and not _BLOCK_TAG.fullmatch(previous.strip()):
                found.append((n, stripped))
            previous = raw
            continue
        found.extend((n, m.group(0)) for m in _INTERP.finditer(raw))
        previous = raw
    return found


@pytest.mark.parametrize("page", MD_PAGES, ids=lambda p: str(p.relative_to(PAGES)))
def test_prose_interpolations_survive_the_markdown_pass(page: Path) -> None:
    text = page.read_text()
    for line, body in prose_interpolations(text):
        assert "_" not in body, (
            f"{page.name}:{line}: `{body}` — an underscore in a prose "
            "interpolation is markdown-emphasised into <em> and breaks the "
            "build. Rename the SQL alias (mode_note -> modenote)."
        )
        assert "'" not in body and '"' not in body, (
            f"{page.name}:{line}: `{body}` — a quote literal in a prose "
            "interpolation is smart-quoted into invalid JS. Move the string "
            "into the SQL query."
        )


@pytest.mark.parametrize("page", MD_PAGES, ids=lambda p: str(p.relative_to(PAGES)))
def test_every_page_declares_its_title_and_place(page: Path) -> None:
    text = page.read_text()
    # The matchup template is a route, not a nav entry.
    if page.name.startswith("["):
        return
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, f"{page.name}: no frontmatter — the tab title falls back to 'Evidence'"
    front = match.group(1)
    assert re.search(r"^title:\s*\S", front, re.M), f"{page.name}: no title"
    # A page inside a folder is ordered by its section, not the top-level nav.
    if page.parent == PAGES:
        assert re.search(r"^sidebar_position:\s*\d", front, re.M), (
            f"{page.name}: no sidebar_position — unnumbered pages sort "
            "alphabetically after the numbered ones, which is how the nav "
            "ended up in the wrong order."
        )


def test_the_nav_order_is_decision_first() -> None:
    """Today, then the board, then the record, then the research."""
    order = {}
    for page in MD_PAGES:
        if page.parent != PAGES or page.name.startswith("["):
            continue
        match = re.search(r"^sidebar_position:\s*(\d+)", page.read_text(), re.M)
        if match:
            order[page.stem] = int(match.group(1))
    assert order["index"] == 1, "Today is the home page and sorts first"
    assert order["board"] < order["performance"] < order["health"]
    assert order["health"] < order["ratings"]
    # Positions are unique, or the tie falls back to filename order.
    assert len(set(order.values())) == len(order), f"duplicate positions: {order}"


# --- the board's typographic rules --------------------------------------
#
# These are the two things the design leans on that a page can silently
# break: a hyphen where a minus belongs, and a coloured number with no sign
# to make the colour redundant. Both are invisible in review and obvious on
# the built page, so they are checked here rather than trusted.

_FMT = re.compile(r"fmt='([^']*)'")
_DELTA_COL = re.compile(r"<Column\b[^>]*contentType=delta[^>]*>", re.S)


@pytest.mark.parametrize("page", MD_PAGES, ids=lambda p: str(p.relative_to(PAGES)))
def test_negative_numbers_use_a_real_minus_sign(page: Path) -> None:
    """U+2212, never a hyphen.

    Every board in this genre sets its negative prices with a true minus.
    A hyphen is narrower than a digit, so a column of ``-110`` next to
    ``+140`` visibly fails to align, and it is the single clearest tell
    that a page was typed rather than designed.
    """
    for match in _FMT.finditer(page.read_text()):
        body = match.group(1)
        negative = body.split(";", 1)[1] if ";" in body else ""
        assert not negative.startswith("-"), (
            f"{page.name}: fmt='{body}' opens its negative section with a "
            "hyphen. Use a real minus sign (−, U+2212)."
        )


@pytest.mark.parametrize("page", MD_PAGES, ids=lambda p: str(p.relative_to(PAGES)))
def test_a_coloured_number_also_carries_its_sign(page: Path) -> None:
    """Colour is the fast read; the sign is the one that always works.

    ``contentType=delta`` colours a cell green or red, which a deuteranope
    cannot separate. The number therefore has to say which way it went on
    its own — either with a printed sign in the format, or with the arrow
    glyph Evidence draws when ``deltaSymbol`` is left on.
    """
    for match in _DELTA_COL.finditer(page.read_text()):
        column = match.group(0)
        fmt = _FMT.search(column)
        signed_format = bool(fmt and fmt.group(1).startswith("+"))
        arrow_shown = "deltaSymbol={false}" not in column
        assert signed_format or arrow_shown, (
            f"{page.name}: `{' '.join(column.split())}` is coloured by sign "
            "but prints neither a sign nor an arrow, so the direction is "
            "carried by hue alone."
        )


def test_the_board_face_is_vendored_not_fetched() -> None:
    """The numeral face ships with the site.

    Pulling it from a font CDN would put a third-party request in front of
    every page load of a private board, and the board would render in the
    fallback until it landed.
    """
    site = PAGES.parent
    layout = (PAGES / "+layout.svelte").read_text()
    assert "fonts.googleapis.com" not in layout and "fonts.gstatic.com" not in layout, (
        "the board face must not be fetched from a font CDN"
    )
    for weight in ("500", "600", "700"):
        face = site / "static" / "fonts" / f"saira-condensed-{weight}.woff2"
        assert face.exists(), f"missing vendored face: {face.name}"
        assert f"saira-condensed-{weight}.woff2" in layout, (
            f"{face.name} is vendored but never declared in the layout"
        )


@pytest.mark.parametrize("page", MD_PAGES, ids=lambda p: str(p.relative_to(PAGES)))
def test_american_prices_are_never_grouped(page: Path) -> None:
    """A board writes +2400, never +2,400.

    Nothing in the genre groups an American price — the comma is what a
    general-purpose number formatter does, and on a ladder market where
    prices run to four figures it is the difference between a board and a
    spreadsheet.
    """
    for match in re.finditer(r"<Column id=(price\w*)\b[^>]*?fmt='([^']*)'", page.read_text()):
        column, fmt = match.groups()
        assert "#,##" not in fmt, (
            f"{page.name}: price column `{column}` uses fmt='{fmt}', which "
            "groups thousands. Use '+0;−0'."
        )
