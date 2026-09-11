"""Team marks and colours — resolution, contrast, and the public-repo line.

Identity is a presentation nicety, which is exactly why it needs pinning: a
club mark that quietly stops resolving costs nothing at test time and shows up
as a page of grey trigrams. Three things are load-bearing.

**Resolution has to accept both spellings.** The projections name NFL clubs by
code ("SEA"); the games frame names them in full ("Seattle Seahawks"). Both are
on the site at once, so a resolver that handles one and not the other leaves
half the surfaces blank.

**Contrast has to be measured, not assumed.** The card renderer's lightness
floor leaves fifteen of thirty-two clubs under 3:1 against the site's panel,
because HLS lightness is not luminance.

**Marks stay hot-linked.** The repo is public and club marks are not ours to
redistribute, so none of them may ever land in the build's static tree.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from velocity.report.assets import (
    TEAM_META,
    contrast_ratio,
    lighten_for_dark,
    readable_on,
    team_identity,
)

# --v-lvl-0: the panel a matchup sheet and a play card sit on.
PANEL = "#0b1017"
# WCAG's bar for non-text graphics, which is what these are: a rule, a hairline.
BAR = 3.0


def test_every_club_colour_clears_the_contrast_bar_on_the_panel() -> None:
    lifted = {code: readable_on(meta.color, PANEL) for code, meta in TEAM_META.items()}
    for code, colour in lifted.items():
        assert contrast_ratio(colour, PANEL) >= BAR - 1e-9, (
            f"{code} lifts to {colour}, only {contrast_ratio(colour, PANEL):.2f}:1"
        )


def test_a_lightness_floor_would_not_have_done_it() -> None:
    """The reason `readable_on` exists rather than a bigger `lighten_for_dark`.

    Lightness is not luminance, so a floor lands wherever the hue happens to be
    bright. The deep blues and purples stay under the bar at *any* floor worth
    using, and the bright hues go garish on the way up.
    """
    for floor in (0.32, 0.50):
        failures = [
            code for code, meta in TEAM_META.items()
            if contrast_ratio(lighten_for_dark(meta.color, floor), PANEL) < BAR
        ]
        assert failures, f"floor {floor} unexpectedly cleared {BAR}:1 for every club"
    # The worst case is the one that motivated it.
    assert contrast_ratio(lighten_for_dark(TEAM_META["BAL"].color, 0.32), PANEL) < 1.6


def test_the_lift_keeps_the_hue_and_leaves_bright_clubs_alone() -> None:
    # Steelers gold and Chiefs red are already clear of the bar, so they are
    # returned byte-identical — a lift nobody needs is a lift that dulls a brand.
    for code in ("PIT", "KC", "CIN", "NO"):
        assert readable_on(TEAM_META[code].color, PANEL) == TEAM_META[code].color
    # The Ravens' purple moves, and is still purple: blue channel dominant,
    # red above green, which is what "purple" means in channel terms.
    lifted = readable_on(TEAM_META["BAL"].color, PANEL).lstrip("#")
    red, green, blue = (int(lifted[i : i + 2], 16) for i in (0, 2, 4))
    assert blue > red > green


def test_nfl_identity_resolves_a_code_and_a_display_name_alike() -> None:
    both = team_identity("nfl", ["SEA", "Seattle Seahawks"])
    assert both["SEA"].code == both["Seattle Seahawks"].code == "SEA"
    assert both["SEA"].logo == both["Seattle Seahawks"].logo
    assert both["SEA"].logo.startswith("https://a.espncdn.com/")
    # The keys round-trip, because callers join back on the string they passed.
    assert both["Seattle Seahawks"].team == "Seattle Seahawks"
    # The two relocated clubs keep the datasets' own codes rather than ESPN's.
    assert team_identity("nfl", ["LA"])["LA"].logo.endswith("/lar.png")


def test_every_team_the_slate_actually_names_resolves() -> None:
    """The check that would have caught the first attempt.

    `TEAM_META` is keyed by nflverse code, so looking a display name up in it
    directly misses all thirty-two — silently, with a trigram that happens to
    be right for Seattle and wrong for New England.
    """
    games = pd.read_parquet("datasets/nfl/games.parquet")
    names = sorted({str(t) for t in pd.concat([games.home_team, games.away_team]).dropna()})
    identities = team_identity("nfl", names)
    unresolved = [name for name in names if identities[name].logo is None]
    assert not unresolved, f"no mark for {unresolved}"


def test_an_unknown_team_still_gets_an_identity() -> None:
    # A blank where a team name belongs is worse than a trigram, so nothing is
    # ever dropped — the mark and the colour are simply absent.
    unknown = team_identity("nfl", ["Nowhere Bobcats"])["Nowhere Bobcats"]
    assert unknown.code == "NOW" and unknown.logo is None and unknown.color is None
    # NCAAF with no key and no cache is the documented degrade path, not an
    # error: every school keeps a code, nobody gets a mark.
    college = team_identity("ncaaf", ["Ohio State", "Ole Miss"], api_key=None, cache_dir=None)
    assert {ident.code for ident in college.values()} == {"OHI", "OLE"}
    assert all(ident.logo is None for ident in college.values())


def test_build_teams_emits_one_hot_linked_row_per_team() -> None:
    from scripts.build_site_data import PANEL as SITE_PANEL
    from scripts.build_site_data import build_teams

    assert SITE_PANEL == PANEL, "the build and this test must measure the same panel"
    games = pd.DataFrame({
        "league": ["nfl", "nfl"],
        "home_team": ["Seattle Seahawks", "Kansas City Chiefs"],
        "away_team": ["New England Patriots", "Seattle Seahawks"],
    })
    table = build_teams(games)
    assert list(table.columns) == ["league", "team", "code", "color", "color_dark", "logo"]
    assert sorted(table.team) == [
        "Kansas City Chiefs", "New England Patriots", "Seattle Seahawks",
    ], "a team on both sides of the slate is still one row"
    assert table.logo.str.startswith("https://a.espncdn.com/").all()
    for row in table.itertuples():
        assert contrast_ratio(row.color_dark, PANEL) >= BAR - 1e-9
    # No games, no table — and still the columns, so the schema holds.
    assert list(build_teams(pd.DataFrame()).columns) == list(table.columns)


def test_club_marks_are_never_vendored_into_the_public_repo() -> None:
    """Kalshi's terms are not the only redistribution line in this repo.

    Club marks are hot-linked from ESPN's public CDN precisely so that none of
    them is committed here. `TeamMark` exists to survive that choice — a page
    has to read correctly with every logo missing — and this is the other half:
    nothing may quietly start shipping them instead.
    """
    static = Path(__file__).resolve().parents[1] / "site" / "static"
    marks = [
        path for path in static.rglob("*")
        if path.is_file() and path.suffix.lower() in {".png", ".svg", ".webp", ".jpg"}
        and "logo" in path.parts[-2:][0].lower() + path.name.lower()
    ]
    assert not marks, f"club marks must not be vendored: {[str(m) for m in marks]}"


def test_an_uncovered_league_resolves_without_touching_the_network(monkeypatch) -> None:
    """The offline suite has to stay offline, and an MLB slate is not college.

    `team_identity` used to send every non-NFL league down the NCAAF branch,
    which calls the CFBD teams endpoint. Locally that is invisible — no
    `CFBD_API_KEY`, so the index comes back empty instantly. In CI the key is
    set, so `build_site_data.py` on the MLB fixture made a live HTTPS request
    from inside the suite that is named *offline*, once per subprocess test.
    """
    import velocity.report.assets as assets

    def explode(*args: object, **kwargs: object) -> dict:
        raise AssertionError("team_identity reached the network for an uncovered league")

    monkeypatch.setattr(assets, "ncaaf_team_index", explode)
    for league in ("mlb", "nhl", "wnba", "ncaab", ""):
        out = assets.team_identity(league, ["Toronto Blue Jays", "Anaheim Ducks"])
        assert [ident.code for ident in out.values()] == ["TOR", "ANA"]
        assert all(ident.logo is None and ident.color is None for ident in out.values())
    # NFL is resolved from the committed table, so it is offline too.
    monkeypatch.setattr(assets, "ncaaf_team_index", explode)
    assert assets.team_identity("nfl", ["SEA"])["SEA"].logo is not None


def test_build_teams_is_offline_for_an_uncovered_league(monkeypatch) -> None:
    import velocity.report.assets as assets
    from scripts.build_site_data import build_teams

    monkeypatch.setattr(
        assets, "ncaaf_team_index",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in build_teams")),
    )
    monkeypatch.setenv("CFBD_API_KEY", "pretend-this-is-real")
    table = build_teams(pd.DataFrame({
        "league": ["mlb"], "home_team": ["Toronto Blue Jays"], "away_team": ["Seattle Mariners"],
    }))
    assert sorted(table.code) == ["SEA", "TOR"]
    assert (table.logo == "").all() and (table.color_dark == "").all()
