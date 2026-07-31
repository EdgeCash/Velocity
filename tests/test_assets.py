"""Card assets — team identity completeness, color math, URLs, and fonts.

No network here: fetches are exercised only through their guard paths (no
cache dir, unknown club, synthetic player id → None). The color rules matter
most — every club must resolve, dark brand colors must lift to visibility,
and a same-color matchup must still produce two distinguishable bar fills.
"""

from __future__ import annotations

from velocity.report.assets import (
    TEAM_META,
    _lightness,
    headshot_path,
    headshot_url,
    lighten_for_dark,
    logo_path,
    logo_url,
    register_fonts,
    team_bar_colors,
)
from velocity.wagering.live import MLB_TEAM_ALIASES


def test_every_club_has_meta() -> None:
    codes = set(MLB_TEAM_ALIASES.values())
    assert codes == set(TEAM_META)
    assert len(TEAM_META) == 30
    for meta in TEAM_META.values():
        assert meta.team_id.isdigit()
        assert meta.color.startswith("#") and len(meta.color) == 7


def test_dark_brand_colors_lift_to_visibility() -> None:
    # Detroit navy (#0C2340) is invisible on the dark surface — it must lift...
    lifted = lighten_for_dark(TEAM_META["DET"].color)
    assert lifted != TEAM_META["DET"].color
    assert _lightness(lifted) >= 0.31  # hex quantization can shave a hair off the floor
    # ...while an already-visible brand color passes through untouched.
    assert lighten_for_dark(TEAM_META["SF"].color) == TEAM_META["SF"].color


def test_same_color_matchup_stays_distinguishable() -> None:
    # STL vs CIN is red-on-red; the away side must step away from the home red.
    away, home = team_bar_colors("STL", "CIN")
    assert away != home
    a = tuple(int(away.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    h = tuple(int(home.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    assert sum(abs(x - y) for x, y in zip(a, h, strict=True)) / 255 >= 0.5
    # An unknown code falls back to the validated default marks.
    away, home = team_bar_colors("???", "LAD")
    assert away == "#d97706"


def test_cdn_urls() -> None:
    assert logo_url("119") == "https://midfield.mlbstatic.com/v1/team/119/spots/128"
    assert headshot_url("660271").endswith("/people/660271/spots/128")


def test_fetch_guards_return_none_without_network() -> None:
    assert logo_path("LAD", None) is None  # no cache dir → never fetches
    assert logo_path("NOPE", "/tmp") is None  # unknown club
    # Synthetic sim ids (league-average stand-ins) have no photo to fetch.
    assert headshot_path("lad_p", "/tmp") is None
    assert headshot_path("fill3", None) is None


def test_fonts_register_with_vendored_families() -> None:
    display, body = register_fonts()
    # The vendored faces resolve when assets/fonts is present (it is, in-repo);
    # the fallback keeps rendering alive either way.
    assert display in ("Barlow Condensed", "DejaVu Sans")
    assert body in ("Barlow", "DejaVu Sans")
    assert register_fonts() == (display, body)  # idempotent
