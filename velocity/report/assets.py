"""Card assets — team identity, MLB CDN images, and the vendored type.

The premium layer for the card renderers: every club's StatsAPI id and brand
color (for logos and team-colored marks), best-effort cached fetches of the
MLB static CDN's spot logos and player headshots, and the Barlow /
Barlow Condensed faces vendored under ``assets/fonts`` (OFL) so the cards
don't render in a stock system font.

Everything degrades: no cache dir → no images; a fetch failure → that image
is skipped; missing fonts → matplotlib's default. A card never fails to
render because a nicety was unavailable.
"""

from __future__ import annotations

import colorsys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from matplotlib import font_manager

_CDN = "https://midfield.mlbstatic.com/v1"
_FETCH_TIMEOUT = 8


@dataclass(frozen=True)
class TeamMeta:
    """A club's StatsAPI id and its brand color (official primary hex)."""

    team_id: str
    color: str


# StatsAPI team ids are stable public constants; colors are the official brand
# primaries (adjusted for the dark surface at use time, not here).
TEAM_META: dict[str, TeamMeta] = {
    "ARI": TeamMeta("109", "#A71930"),
    "ATL": TeamMeta("144", "#CE1141"),
    "BAL": TeamMeta("110", "#DF4601"),
    "BOS": TeamMeta("111", "#BD3039"),
    "CHC": TeamMeta("112", "#0E3386"),
    "CWS": TeamMeta("145", "#C4CED4"),
    "CIN": TeamMeta("113", "#C6011F"),
    "CLE": TeamMeta("114", "#E31937"),
    "COL": TeamMeta("115", "#5F259F"),
    "DET": TeamMeta("116", "#0C2340"),
    "HOU": TeamMeta("117", "#EB6E1F"),
    "KC": TeamMeta("118", "#004687"),
    "LAA": TeamMeta("108", "#BA0021"),
    "LAD": TeamMeta("119", "#005A9C"),
    "MIA": TeamMeta("146", "#00A3E0"),
    "MIL": TeamMeta("158", "#FFC52F"),
    "MIN": TeamMeta("142", "#002B5C"),
    "NYM": TeamMeta("121", "#FF5910"),
    "NYY": TeamMeta("147", "#003087"),
    "ATH": TeamMeta("133", "#003831"),
    "PHI": TeamMeta("143", "#E81828"),
    "PIT": TeamMeta("134", "#FDB827"),
    "SD": TeamMeta("135", "#FFC425"),
    "SEA": TeamMeta("136", "#005C5C"),
    "SF": TeamMeta("137", "#FD5A1E"),
    "STL": TeamMeta("138", "#C41E3A"),
    "TB": TeamMeta("139", "#8FBCE6"),
    "TEX": TeamMeta("140", "#003278"),
    "TOR": TeamMeta("141", "#134A8E"),
    "WSH": TeamMeta("120", "#AB0003"),
}


def _hex_to_rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{int(round(c * 255)):02x}" for c in rgb)


def _lightness(color: str) -> float:
    return colorsys.rgb_to_hls(*_hex_to_rgb(color))[1]


def lighten_for_dark(color: str, floor: float = 0.32) -> str:
    """Raise a too-dark brand color to a visible lightness on the dark surface.

    Hue is preserved — a navy stays navy, just readable. Colors already above
    the floor pass through unchanged.
    """
    h, lightness, s = colorsys.rgb_to_hls(*_hex_to_rgb(color))
    if lightness >= floor:
        return color
    return _rgb_to_hex(colorsys.hls_to_rgb(h, floor, s))


def team_bar_colors(away_code: str, home_code: str) -> tuple[str, str]:
    """Display colors for the away/home win-split bar, guaranteed distinct.

    Both brand colors are lifted to dark-surface visibility; when the pair is
    still too similar (a red-vs-red matchup), the away side steps further
    toward white so the two segments never merge. Identity also rides on the
    direct code labels, so color is never the only cue.
    """
    away = lighten_for_dark(TEAM_META[away_code].color) if away_code in TEAM_META else "#d97706"
    home = lighten_for_dark(TEAM_META[home_code].color) if home_code in TEAM_META else "#0d9488"
    a_rgb, h_rgb = _hex_to_rgb(away), _hex_to_rgb(home)
    distance = sum(abs(a - b) for a, b in zip(a_rgb, h_rgb, strict=True))
    if distance < 0.55:
        h, lightness, s = colorsys.rgb_to_hls(*a_rgb)
        away = _rgb_to_hex(colorsys.hls_to_rgb(h, min(lightness + 0.28, 0.85), max(s * 0.7, 0.1)))
    return away, home


def logo_url(team_id: str, size: int = 128) -> str:
    return f"{_CDN}/team/{team_id}/spots/{size}"


def headshot_url(player_id: str, size: int = 128) -> str:
    return f"{_CDN}/people/{player_id}/spots/{size}"


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
    """Cached spot-logo PNG for a club code, or None (unknown club / no cache)."""
    meta = TEAM_META.get(team_code)
    if meta is None or cache_dir is None:
        return None
    return _fetch(logo_url(meta.team_id), Path(cache_dir) / f"logo_{meta.team_id}.png")


def headshot_path(player_id: str, cache_dir: Path | str | None) -> Path | None:
    """Cached headshot PNG for a StatsAPI player id, or None."""
    if cache_dir is None or not str(player_id).isdigit():
        return None  # synthetic ids (league-average stand-ins) have no photo
    return _fetch(headshot_url(str(player_id)), Path(cache_dir) / f"head_{player_id}.png")


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
        # Mono faces belong to the terminal card; this pair is the body/display
        # family for the graphic cards and must not be displaced by them.
        for path in sorted(_FONT_DIR.glob("*.ttf")):
            if "Mono" in path.stem:
                continue
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
