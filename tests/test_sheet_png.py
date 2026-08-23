"""Sheet composition — the one all-inclusive pregame graphic."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from velocity.report.sheet_png import SEAM_HEIGHT, compose_sheets, sheet_filename


def _png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> Path:
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


def test_sheet_filename() -> None:
    name = sheet_filename("social_nfl_20260101T000000Z_BUF_at_KC.png")
    assert name == "sheet_nfl_20260101T000000Z_BUF_at_KC.png"


def test_compose_stacks_card_over_dive(tmp_path: Path) -> None:
    card = _png(tmp_path / "social_nfl_s_A_at_B.png", (160, 90), (10, 10, 10))
    dive = _png(tmp_path / "deepdive_nfl_s_A_at_B.png", (160, 90), (200, 0, 0))
    sheets = compose_sheets({"g1": card}, {"g1": dive}, tmp_path)
    out = Image.open(sheets["g1"])
    assert out.size == (160, 180 + SEAM_HEIGHT)
    assert out.getpixel((80, 10)) == (10, 10, 10)      # card on top
    assert out.getpixel((80, 90 + SEAM_HEIGHT + 10)) == (200, 0, 0)  # dive below


def test_compose_without_dive_is_the_card(tmp_path: Path) -> None:
    card = _png(tmp_path / "social_nfl_s_C_at_D.png", (160, 90), (5, 5, 5))
    sheets = compose_sheets({"g2": card}, {}, tmp_path)
    out = Image.open(sheets["g2"])
    assert out.size == (160, 90)
    assert sheets["g2"].name == "sheet_nfl_s_C_at_D.png"


def test_compose_resizes_mismatched_widths(tmp_path: Path) -> None:
    card = _png(tmp_path / "social_x_s_E_at_F.png", (160, 90), (1, 1, 1))
    dive = _png(tmp_path / "deepdive_x_s_E_at_F.png", (80, 45), (2, 2, 2))
    sheets = compose_sheets({"g3": card}, {"g3": dive}, tmp_path)
    assert Image.open(sheets["g3"]).size == (160, 180 + SEAM_HEIGHT)
