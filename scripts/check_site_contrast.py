"""Contrast gate for the hub's palette — every pair a reader actually sees.

    python scripts/check_site_contrast.py

Reads the design tokens straight out of ``site/pages/+layout.svelte`` (there is
no second copy of them, by design) and checks every foreground against every
surface it can land on, every tinted pill against its own tint, and every fill
against the ink that sits on it. Exits non-zero on the first pair under 4.5:1.

**Why this exists.** The park palette is taken from Ballpark Pal's own
stylesheets, and their pairs do not clear WCAG AA: cream on their dirt band is
3.79:1 and white on their grass green is 2.78:1. They carry it at 75px
headers; this surface runs a quarter that size, where the same colors are
unreadable. So the hues are theirs and the luminances are ours — and a gate is
the only thing that keeps a later "let's brighten the green" from quietly
undoing it.

4.5:1 is the AA bar for body text. Everything here is treated as body text on
purpose: the hub's labels run to 0.56rem, and a rule with an exception for
"large" text is a rule that gets argued with.
"""

from __future__ import annotations

import re
from pathlib import Path

LAYOUT = Path("site/pages/+layout.svelte")
FLOOR = 4.5

# Foregrounds: anything that is ever set as type.
INKS = ("v-ink", "v-ink-2", "v-ink-3", "v-brand", "v-brand-dim", "v-pos",
        "v-neg", "v-warn", "v-info", "v-alert", "v-thin")
# Surfaces type can land on.
SURFACES = (("page", "v-bg"), ("card", "v-lvl-1"), ("raised", "v-lvl-2"),
            ("well", "v-lvl-0"), ("chip", "v-chip"))
# Colored fills, and the token whose ink goes on top of each.
FILLS = (("v-grass", "v-ink"), ("v-band", "v-band-ink"), ("v-brand-deep", "v-brand"))
# Foregrounds worn on a pill of their own tint.
TINTED = ("v-pos", "v-neg", "v-warn", "v-info", "v-brand", "v-thin")
# The dirt band is a second ground: `.topbar` re-points --v-ink and friends at
# these, so the ticker and the stamp render on dirt without either component
# knowing. Which means every one of them is type on #6b5442 and belongs here —
# the page-surface table above would never look at them.
BAND_INKS = ("v-band-ink", "v-band-ink-2", "v-band-ink-3", "v-band-brand",
             "v-band-warn", "v-band-alert", "v-band-pos")


def _srgb(channel: float) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    """Relative luminance of a ``#rgb`` or ``#rrggbb`` color."""
    h = color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two opaque colors."""
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def composite(fg: str, alpha: float, bg: str) -> str:
    """``fg`` at ``alpha`` over ``bg``, as an opaque hex — what a tint really is."""
    f, b = fg.lstrip("#"), bg.lstrip("#")
    out = (round(int(f[i:i + 2], 16) * alpha + int(b[i:i + 2], 16) * (1 - alpha))
           for i in (0, 2, 4))
    return "#" + "".join(f"{c:02x}" for c in out)


def read_tokens(path: Path = LAYOUT) -> dict[str, str]:
    """The ``--v-*`` custom properties from the layout's ``:root`` block."""
    src = path.read_text()
    start = src.index(":global(:root)")
    block = src[start:src.index("}", start)]
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"--(v-[\w-]+):\s*([^;]+);", block)}


def tint_alpha(value: str) -> float:
    """The alpha out of an ``rgba(r, g, b, a)`` token; 0.13 if it is not one."""
    match = re.search(r"([\d.]+)\s*\)\s*$", value)
    return float(match.group(1)) if match and value.startswith("rgba") else 0.13


def failures(tokens: dict[str, str], floor: float = FLOOR) -> list[str]:
    """Every pair under ``floor``, as readable lines. Empty means the palette ships."""
    bad: list[str] = []
    for ink in INKS:
        for label, surface in SURFACES:
            ratio = contrast(tokens[ink], tokens[surface])
            if ratio < floor:
                bad.append(f"{ink} on {label} = {ratio:.2f}:1")
    for fill, ink in FILLS:
        ratio = contrast(tokens[ink], tokens[fill])
        if ratio < floor:
            bad.append(f"{ink} on {fill} fill = {ratio:.2f}:1")
    for ink in TINTED:
        tint = tokens.get(f"{ink}-tint", "")
        pill = composite(tokens[ink], tint_alpha(tint), tokens["v-bg"])
        ratio = contrast(tokens[ink], pill)
        if ratio < floor:
            bad.append(f"{ink} on its own tint = {ratio:.2f}:1")
    for ink in BAND_INKS:
        ratio = contrast(tokens[ink], tokens["v-band"])
        if ratio < floor:
            bad.append(f"{ink} on the dirt band = {ratio:.2f}:1")
    return bad


def main() -> None:
    tokens = read_tokens()
    header = f"{'token':16s}" + "".join(f"{name:>9s}" for name, _ in SURFACES)
    print(header)
    for ink in INKS:
        row = f"  {ink:14s}"
        for _, surface in SURFACES:
            row += f"{contrast(tokens[ink], tokens[surface]):9.2f}"
        print(row)
    print("\n  fills:")
    for fill, ink in FILLS:
        print(f"    {contrast(tokens[ink], tokens[fill]):5.2f}  {ink} on {fill}")
    print("\n  tinted pills:")
    for ink in TINTED:
        pill = composite(tokens[ink], tint_alpha(tokens.get(f"{ink}-tint", "")), tokens["v-bg"])
        print(f"    {contrast(tokens[ink], pill):5.2f}  {ink} on {ink}-tint")
    print("\n  the dirt band's own ground:")
    for ink in BAND_INKS:
        print(f"    {contrast(tokens[ink], tokens['v-band']):5.2f}  {ink} on v-band")

    bad = failures(tokens)
    if bad:
        print(f"\n{len(bad)} pair(s) under {FLOOR}:1 — the palette does not ship:")
        for line in bad:
            print(f"  - {line}")
        raise SystemExit(1)
    print(f"\nevery pair clears {FLOOR}:1")


if __name__ == "__main__":
    main()
