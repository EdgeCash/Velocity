"""Card assets — NFL team identity, logo CDN fetches, and the vendored type.

The premium layer for the card renderers: every club's brand color (for
team-colored marks) and ESPN logo slug, best-effort cached fetches of the
public logo CDN, and the Barlow / Barlow Condensed faces vendored under
``assets/fonts`` (OFL) so the cards don't render in a stock system font.

Everything degrades: no cache dir → no images; a fetch failure → that image
is skipped; missing fonts → matplotlib's default. A card never fails to
render because a nicety was unavailable.
"""

from __future__ import annotations

import colorsys
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from matplotlib import font_manager

# ESPN's public team-logo CDN, addressed by their lowercase NFL slug.
_LOGO_CDN = "https://a.espncdn.com/i/teamlogos/nfl/500"
_FETCH_TIMEOUT = 8


@dataclass(frozen=True)
class TeamMeta:
    """A club's ESPN logo slug and its brand color (official primary hex)."""

    logo_slug: str
    color: str


# Keys are nflverse team codes (the codes the ratings and aliases use).
# Colors are the official brand primaries — adjusted for the dark surface at
# use time (lighten_for_dark), not here.
TEAM_META: dict[str, TeamMeta] = {
    "ARI": TeamMeta("ari", "#97233F"),
    "ATL": TeamMeta("atl", "#A71930"),
    "BAL": TeamMeta("bal", "#241773"),
    "BUF": TeamMeta("buf", "#00338D"),
    "CAR": TeamMeta("car", "#0085CA"),
    "CHI": TeamMeta("chi", "#C83803"),
    "CIN": TeamMeta("cin", "#FB4F14"),
    "CLE": TeamMeta("cle", "#FF3C00"),
    "DAL": TeamMeta("dal", "#041E42"),
    "DEN": TeamMeta("den", "#FB4F14"),
    "DET": TeamMeta("det", "#0076B6"),
    "GB": TeamMeta("gb", "#203731"),
    "HOU": TeamMeta("hou", "#03202F"),
    "IND": TeamMeta("ind", "#002C5F"),
    "JAX": TeamMeta("jax", "#006778"),
    "KC": TeamMeta("kc", "#E31837"),
    "LA": TeamMeta("lar", "#003594"),
    "LAC": TeamMeta("lac", "#0080C6"),
    "LV": TeamMeta("lv", "#A5ACAF"),
    "MIA": TeamMeta("mia", "#008E97"),
    "MIN": TeamMeta("min", "#4F2683"),
    "NE": TeamMeta("ne", "#002244"),
    "NO": TeamMeta("no", "#D3BC8D"),
    "NYG": TeamMeta("nyg", "#0B2265"),
    "NYJ": TeamMeta("nyj", "#125740"),
    "PHI": TeamMeta("phi", "#004C54"),
    "PIT": TeamMeta("pit", "#FFB612"),
    "SEA": TeamMeta("sea", "#002244"),
    "SF": TeamMeta("sf", "#AA0000"),
    "TB": TeamMeta("tb", "#D50A0A"),
    "TEN": TeamMeta("ten", "#0C2340"),
    "WAS": TeamMeta("was", "#5A1414"),
}


def _hex_to_rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{int(round(c * 255)):02x}" for c in rgb)


def lighten_for_dark(color: str, floor: float = 0.32) -> str:
    """Raise a too-dark brand color to a visible lightness on the dark surface.

    Hue is preserved — a navy stays navy, just readable. Colors already above
    the floor pass through unchanged.
    """
    h, lightness, s = colorsys.rgb_to_hls(*_hex_to_rgb(color))
    if lightness >= floor:
        return color
    return _rgb_to_hex(colorsys.hls_to_rgb(h, floor, s))


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG relative luminance — the perceptual weighting, not HLS lightness."""
    channels = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(color: str, background: str) -> float:
    """WCAG contrast between two hex colors, 1.0 (identical) to 21.0."""
    a, b = _relative_luminance(_hex_to_rgb(color)), _relative_luminance(_hex_to_rgb(background))
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def readable_on(color: str, background: str, *, ratio: float = 3.0) -> str:
    """Raise a brand color until it actually contrasts with ``background``.

    :func:`lighten_for_dark` raises HLS *lightness* to a floor, which is the
    right idea in the wrong space: lightness is not luminance, so the floor
    lands wherever the hue happens to be bright. Measured against the site's
    near-black panel, a 0.32 floor leaves fifteen of the thirty-two clubs under
    3:1 and the Ravens' purple at 1.5:1 — a 2px rule in it reads as a dark line,
    not as purple. Raising the floor does not fix it either: at 0.50 the deep
    blues and purples are *still* under 3:1, and the bright hues have gone
    garish on the way.

    So this searches lightness for the contrast instead of guessing at it. Hue
    and saturation are held, so a navy stays navy and a purple stays purple;
    only how light it is moves, and only as far as the target needs. A color
    already clear of the bar is returned untouched. 3:1 is the WCAG bar for
    non-text graphics, which is what these are — a rule, a hairline, a chip.
    """
    if contrast_ratio(color, background) >= ratio:
        return color
    hue, lightness, saturation = colorsys.rgb_to_hls(*_hex_to_rgb(color))
    low, high = lightness, 1.0
    for _ in range(32):  # ~1e-10 of lightness; far finer than 8-bit output
        mid = (low + high) / 2.0
        candidate = colorsys.hls_to_rgb(hue, mid, saturation)
        if contrast_ratio(_rgb_to_hex(candidate), background) >= ratio:
            high = mid
        else:
            low = mid
    return _rgb_to_hex(colorsys.hls_to_rgb(hue, high, saturation))


def bar_colors(away_hex: str | None, home_hex: str | None) -> tuple[str, str]:
    """Display colors for the away/home win-split bar, guaranteed distinct.

    Both brand colors are lifted to dark-surface visibility; when the pair is
    still too similar (a red-vs-red matchup), the away side steps further
    toward white so the two segments never merge. Identity also rides on the
    direct code labels, so color is never the only cue. ``None`` sides fall
    back to the neutral amber/teal pair.
    """
    away = lighten_for_dark(away_hex) if away_hex else "#d97706"
    home = lighten_for_dark(home_hex) if home_hex else "#0d9488"
    a_rgb, h_rgb = _hex_to_rgb(away), _hex_to_rgb(home)
    distance = sum(abs(a - b) for a, b in zip(a_rgb, h_rgb, strict=True))
    if distance < 0.55:
        h, lightness, s = colorsys.rgb_to_hls(*a_rgb)
        away = _rgb_to_hex(colorsys.hls_to_rgb(h, min(lightness + 0.28, 0.85), max(s * 0.7, 0.1)))
    return away, home


def team_bar_colors(away_code: str, home_code: str) -> tuple[str, str]:
    """The win-split pair for NFL club codes (the fixed brand table)."""
    away_meta = TEAM_META.get(away_code)
    home_meta = TEAM_META.get(home_code)
    return bar_colors(
        away_meta.color if away_meta else None,
        home_meta.color if home_meta else None,
    )


def logo_url(logo_slug: str) -> str:
    return f"{_LOGO_CDN}/{logo_slug}.png"


def _fetch(url: str, dest: Path) -> Path | None:
    """Fetch to the cache once; None on any failure (the card just skips it)."""
    if dest.exists():
        return dest
    try:  # pragma: no cover - network
        dest.parent.mkdir(parents=True, exist_ok=True)
        # The CDN 403s the default Python UA; identify as an ordinary client.
        request = urllib.request.Request(  # noqa: S310 - fixed https host
            url, headers={"User-Agent": "Mozilla/5.0 (MatchUpLabs card renderer)"}
        )
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
            dest.write_bytes(resp.read())
        return dest
    except Exception:  # noqa: BLE001 - a missing image is cosmetic, never fatal
        return None


def logo_path(team_code: str, cache_dir: Path | str | None) -> Path | None:
    """Cached logo PNG for a club code, or None (unknown club / no cache)."""
    meta = TEAM_META.get(team_code)
    if meta is None or cache_dir is None:
        return None
    return _fetch(logo_url(meta.logo_slug), Path(cache_dir) / f"logo_{meta.logo_slug}.png")


# --- NCAAF school identity ----------------------------------------------------
#
# The base identity is the school's abbreviation plus its official colors —
# plain facts, always available. School marks now ALSO render where the owner
# opted in (the broadcast grid; 2026-09 decision, mirroring the NFL cards'
# public-CDN logo treatment): fetched best-effort from the ESPN CDN by the
# ESPN team id CFBD's teams payload carries, cached, and always degrading to
# the abbreviation chip when a fetch fails. Player imagery stays out.
# The identity table comes from the CFBD teams endpoint (the same source the
# model's schedule uses), cached to the asset dir; with no API key the cards
# fall back to provider names and the neutral bar colors.

_CFBD_TEAMS_URL = "https://api.collegefootballdata.com/teams/fbs"
_ESPN_NCAA_LOGO = "https://a.espncdn.com/i/teamlogos/ncaa/500"
_ESPN_LEAGUE_LOGO = "https://a.espncdn.com/i/teamlogos/leagues/500"


@dataclass(frozen=True)
class SchoolMeta:
    """A school's display abbreviation, official colors, and ESPN team id."""

    abbreviation: str
    color: str | None = None
    alt_color: str | None = None
    espn_id: int | None = None


def ncaaf_logo_path(espn_id: int | None, cache_dir: Path | str | None) -> Path | None:
    """Cached ESPN-CDN school mark for a CFBD/ESPN team id, or None."""
    if espn_id is None or cache_dir is None:
        return None
    return _fetch(f"{_ESPN_NCAA_LOGO}/{int(espn_id)}.png",
                  Path(cache_dir) / f"logo_ncaa_{int(espn_id)}.png")


def league_logo_path(league: str, cache_dir: Path | str | None) -> Path | None:
    """Cached ESPN-CDN league mark, or None.

    Only the NFL shield exists at the leagues path (the NCAA slugs 404 —
    probed 2026-09); college grids simply lead with the title text.
    """
    slug = {"nfl": "nfl"}.get(league)
    if slug is None or cache_dir is None:
        return None
    return _fetch(f"{_ESPN_LEAGUE_LOGO}/{slug}.png",
                  Path(cache_dir) / f"logo_league_{slug}.png")


def parse_ncaaf_teams(payload: list[dict]) -> dict[str, SchoolMeta]:
    """CFBD ``/teams/fbs`` payload → ``{school: SchoolMeta}``.

    Rows without a school name are dropped; a missing abbreviation falls back
    to the school name uppercased (the renderer auto-shrinks long labels).
    Colors pass through only when they look like hex.
    """

    def _hex(value: object) -> str | None:
        s = str(value or "")
        if not s.startswith("#"):
            s = f"#{s}" if s else ""
        return s if len(s) == 7 else None

    out: dict[str, SchoolMeta] = {}
    for row in payload:
        school = row.get("school")
        if not school:
            continue
        abbrev = str(row.get("abbreviation") or school).upper()
        try:
            espn_id = int(row["id"]) if row.get("id") is not None else None
        except (TypeError, ValueError):
            espn_id = None
        out[str(school)] = SchoolMeta(
            abbreviation=abbrev,
            color=_hex(row.get("color")),
            alt_color=_hex(row.get("altColor") or row.get("alt_color")),
            espn_id=espn_id,
        )
    return out


def ncaaf_team_index(
    api_key: str | None, cache_dir: Path | str | None
) -> dict[str, SchoolMeta]:  # pragma: no cover - network + cache orchestration
    """The FBS identity table, fetched once and cached; ``{}`` on any failure."""
    import json

    cache = None if cache_dir is None else Path(cache_dir) / "ncaaf_teams.json"
    if cache is not None and cache.exists():
        try:
            return parse_ncaaf_teams(json.loads(cache.read_text()))
        except Exception:  # noqa: BLE001 - a corrupt cache entry just refetches
            pass
    if not api_key:
        return {}
    try:
        request = urllib.request.Request(  # noqa: S310 - fixed https host
            _CFBD_TEAMS_URL, headers={"Authorization": f"Bearer {api_key}"}
        )
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
            payload = json.loads(resp.read())
    except Exception:  # noqa: BLE001 - identity is a nicety, never fatal
        return {}
    if cache is not None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(payload))
        except Exception:  # noqa: BLE001 - caching is best-effort
            pass
    return parse_ncaaf_teams(payload)


# --- identity for a rendering surface ----------------------------------------
#
# The card renderers each resolve identity their own way because each needs a
# cached *file* for matplotlib. A web surface needs the CDN URL instead, and the
# alias work in between is the same either way — so it lives here once rather
# than as a fourth copy of the nickname bridge.


# The leagues :func:`team_identity` can actually resolve. Everything else is
# codes only, resolved without a network call of any kind.
IDENTITY_LEAGUES = frozenset({"nfl", "ncaaf"})


@dataclass(frozen=True)
class TeamIdentity:
    """What a surface needs to draw a team: a code, a brand color, and a mark.

    ``team`` is the key the caller passed in, so a lookup round-trips. ``color``
    is the official primary, **unadjusted** — a surface that draws on dark
    applies :func:`lighten_for_dark` at use time, the same split the card
    renderers use. Either of ``color`` and ``logo`` may be ``None``: a school
    with no banked color, or a league with no mark, still gets a code.
    """

    team: str
    code: str
    color: str | None = None
    logo: str | None = None


def ncaaf_logo_url(espn_id: int | None) -> str | None:
    """The public ESPN-CDN school mark for a CFBD/ESPN team id, or ``None``."""
    return None if espn_id is None else f"{_ESPN_NCAA_LOGO}/{int(espn_id)}.png"


def team_identity(
    league: str,
    teams: Iterable[str],
    *,
    api_key: str | None = None,
    cache_dir: Path | str | None = None,
) -> dict[str, TeamIdentity]:
    """Identity for each name in ``teams``, keyed by the name as given.

    Names arrive as whatever the surface already carries, which is not one
    thing: the projections name NFL clubs by nflverse code ("SEA") while the
    games frame names them in full ("Seattle Seahawks"). Both resolve, through
    the same :func:`~velocity.wagering.live.resolve_team` order the slate uses —
    exact code, then the alias table, then a unique normalized match — so a
    caller never has to know which of the two it is holding. NCAAF names are
    whatever the provider called the school, bridged to the CFBD index by the
    nickname-prefix logic the same module provides; otherwise "Georgia
    Bulldogs" and "Georgia" would be two different teams on the same page.

    A name nothing can place still comes back, with an upper-cased trigram and
    no color or mark. Identity is a presentation nicety; a team the table has
    never heard of must still render, and the surfaces here all fall back to
    the code chip.

    Only the leagues this module actually has identity for consult anything.
    A league it does not cover gets codes and returns **without touching the
    network** — treating every non-NFL league as college football would have a
    site build for an MLB slate calling a college-football endpoint, and would
    put a live request inside an offline test suite.
    """
    from velocity.wagering.live import nickname_aliases, resolve_team

    names = [str(team) for team in teams if str(team)]
    out: dict[str, TeamIdentity] = {}
    key = str(league).lower()
    if key not in IDENTITY_LEAGUES:
        return {name: TeamIdentity(team=name, code=name[:3].upper()) for name in names}
    if key == "nfl":
        for name in names:
            code = resolve_team(name, TEAM_META.keys())
            club = TEAM_META.get(code) if code else None
            out[name] = TeamIdentity(
                team=name,
                code=code or name[:3].upper(),
                color=club.color if club else None,
                logo=logo_url(club.logo_slug) if club else None,
            )
        return out

    index = ncaaf_team_index(api_key, cache_dir)
    # An empty index is the documented degrade path (no key, no cache, a failed
    # fetch): every name keeps its trigram and the surface shows chips.
    schools = nickname_aliases(names, index.keys()) if index else {}
    for name in names:
        school = index.get(schools.get(name, name))
        if school is None:
            out[name] = TeamIdentity(team=name, code=name[:3].upper())
            continue
        out[name] = TeamIdentity(
            team=name,
            code=school.abbreviation,
            color=school.color or school.alt_color,
            logo=ncaaf_logo_url(school.espn_id),
        )
    return out


_FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
_registered = False


def register_fonts() -> tuple[str, str]:
    """Register the vendored faces; return (display_family, body_family).

    Barlow Condensed carries headlines and hero numbers; Barlow carries body
    text. Falls back to matplotlib's default family when the files are absent
    (a fresh checkout without assets still renders).
    """
    global _registered
    display, body = "DejaVu Sans", "DejaVu Sans"
    try:
        for path in sorted(_FONT_DIR.glob("*.ttf")):
            if not _registered:
                font_manager.fontManager.addfont(str(path))
            name = font_manager.FontProperties(fname=str(path)).get_name()
            if "Condensed" in path.stem:
                display = name
            else:
                body = name
        _registered = True
    except Exception:  # noqa: BLE001 - typography is a nicety, never fatal
        pass
    return display, body
