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
