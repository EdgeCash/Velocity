"""The sheet — one all-inclusive pregame graphic per game.

Composes the MARKET vs MODEL card and its Deep Dive companion into a
single tall PNG (1600×1800, the bettorsheets-style scrolling sheet that
posts well on both X and Instagram). Pure image composition: the two
source renders already share the canvas width and background, so the
sheet inherits their quality with zero re-layout risk. A game whose deep
dive was skipped (missing form data) still gets a sheet — just the card
alone.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

# The seam drawn between the two panels, in the site border color.
SEAM_COLOR = (29, 39, 51)
SEAM_HEIGHT = 4


def sheet_filename(social_name: str) -> str:
    """``social_{league}_{stamp}_{A}_at_{H}.png`` → the sheet's filename."""
    if not social_name.startswith("social_"):
        raise ValueError(f"not a social card filename: {social_name}")
    return "sheet_" + social_name[len("social_"):]


def compose_sheet(card: Path, dive: Path | None, out: Path) -> Path:
    """Stack ``card`` above ``dive`` (when present) into ``out``."""
    top = Image.open(card).convert("RGB")
    if dive is None:
        top.save(out, format="PNG")
        return out
    bottom = Image.open(dive).convert("RGB")
    if bottom.width != top.width:
        ratio = top.width / bottom.width
        bottom = bottom.resize((top.width, round(bottom.height * ratio)))
    sheet = Image.new(
        "RGB", (top.width, top.height + SEAM_HEIGHT + bottom.height), SEAM_COLOR
    )
    sheet.paste(top, (0, 0))
    sheet.paste(bottom, (0, top.height + SEAM_HEIGHT))
    sheet.save(out, format="PNG")
    return out


def compose_sheets(
    card_paths: dict[str, Path],
    dive_paths: dict[str, Path],
    out_dir: Path,
) -> dict[str, Path]:
    """Compose one sheet per game: ``{game_id: social path}`` × dives → sheets.

    Returns ``{game_id: sheet path}``. Source PNGs are left in place —
    the caller decides whether they survive into the artifact.
    """
    sheets: dict[str, Path] = {}
    for game_id, card in card_paths.items():
        out = out_dir / sheet_filename(card.name)
        sheets[game_id] = compose_sheet(card, dive_paths.get(game_id), out)
    return sheets
