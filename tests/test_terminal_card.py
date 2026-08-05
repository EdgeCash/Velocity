"""Terminal card — the character grid is data, so its output is pinned exactly.

The whole card is built as positioned, colored spans before anything is
painted, which makes the retro aesthetic as testable as a table: block-bar
heights, histogram bucketing (no folded tail spikes, no comb gaps), spread
formatting in the repo's sign convention, and the two-phosphor rule that green
carries the model and amber carries the market.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pytest
from velocity.report.terminal_card import (
    AMBER,
    COLS,
    GREEN,
    ROWS,
    TerminalCard,
    _fmt_spread,
    card_lines,
    histogram_rows,
    probability_bar,
    render_terminal_card,
)


def _card(**kw: object) -> TerminalCard:
    base: dict = {
        "league": "NCAAF",
        "away_code": "GT", "home_code": "UGA",
        "away_name": "Georgia Tech", "home_name": "Georgia",
        "kickoff": "SAT NOV 30 · 7:30 PM ET", "week": "WK 14 · 2024",
        "p_home_win": 0.91,
        "mu_away": 14.8, "mu_home": 37.9,
        "fair_spread": -23.0, "fair_total": 53.0,
        "total_pmf": {40: 0.1, 45: 0.2, 50: 0.4, 55: 0.2, 60: 0.1},
        "n_sims": 10_000,
        "market_spread": 17.5, "market_total": 49.0,
    }
    base.update(kw)
    return TerminalCard(**base)  # type: ignore[arg-type]


# --- block primitives ---------------------------------------------------------


def test_probability_bar_splits_filled_and_empty() -> None:
    filled, empty = probability_bar(0.25, 40)
    assert filled == "█" * 10
    assert empty == "░" * 30
    assert probability_bar(0.0, 8) == ("", "░" * 8)
    assert probability_bar(1.0, 8) == ("█" * 8, "")
    # Out-of-range inputs clamp rather than overflow the bar.
    assert probability_bar(1.7, 8) == ("█" * 8, "")


def test_histogram_is_full_height_at_the_peak() -> None:
    rows = histogram_rows({10: 0.5, 11: 0.25, 12: 0.25}, 10, 13, width=3, rows=4)
    assert len(rows) == 4
    assert all(len(line) == 3 for line in rows)
    # The modal bucket reaches the top row; the half-height ones do not.
    assert rows[0][0] == "█"
    assert rows[0][1] == " "


def test_histogram_drops_out_of_range_instead_of_folding() -> None:
    """A trimmed tail must not pile into the end column as a phantom spike."""
    pmf = {10: 0.5, 11: 0.5, 99: 0.5}  # 99 sits far outside the drawn range
    rows = histogram_rows(pmf, 10, 12, width=2, rows=4)
    # Both drawn columns are equal height; nothing inherits the 99.
    assert rows[0][0] == rows[0][1] == "█"


def test_histogram_handles_degenerate_input() -> None:
    assert histogram_rows({}, 0, 10, width=5, rows=3) == ["     "] * 3
    assert histogram_rows({1: 1.0}, 5, 5, width=5, rows=3) == ["     "] * 3


def test_spread_formats_by_favorite() -> None:
    # Repo convention: a negative home line means the home side is favored.
    assert _fmt_spread("UGA", "GT", -23.0) == "UGA -23.0"
    assert _fmt_spread("UGA", "GT", 6.5) == "GT -6.5"
    assert _fmt_spread("UGA", "GT", 0.0) == "PK"


# --- the grid -----------------------------------------------------------------


def _text_at(spans: list, row: int) -> str:
    """Flatten one grid row's spans into a plain string, for content assertions."""
    line = [" "] * (COLS + 40)
    for span in spans:
        if span.row == row:
            for i, ch in enumerate(span.text):
                line[span.col + i] = ch
    return "".join(line).rstrip()


def test_grid_stays_inside_its_bounds() -> None:
    spans = card_lines(_card())
    assert spans, "the card must render something"
    for span in spans:
        assert 0 <= span.row < ROWS, f"row {span.row} outside the grid"
        assert span.col >= 0
        assert span.col + len(span.text) <= COLS, f"'{span.text}' overruns the frame"


def test_frame_closes_on_every_row() -> None:
    spans = card_lines(_card())
    top = _text_at(spans, 0)
    assert top.startswith("┌") and top.endswith("┐")
    assert len(top) == COLS
    bottom = _text_at(spans, ROWS - 1)
    assert bottom.startswith("└") and bottom.endswith("┘")
    assert len(bottom) == COLS
    for row in range(1, ROWS - 1):  # every interior row carries both side rules
        line = _text_at(spans, row)
        assert line.startswith("│")
        assert line[COLS - 1] == "│"


def test_model_is_green_and_market_is_amber() -> None:
    """The two-phosphor rule: green states the model, amber states the market."""
    spans = card_lines(_card())
    model_header = next(s for s in spans if s.text == "[ MODEL ]")
    market_header = next(s for s in spans if s.text == "[ MARKET ]")
    assert model_header.color == GREEN
    assert market_header.color == AMBER
    # Every market value is amber; the model's numbers never are.
    amber = {s.text for s in spans if s.color == AMBER}
    assert "UGA -17.5" in amber  # market spread, favorite-first
    assert "O/U 49.0" in amber
    assert "+4.0 PTS" in amber  # model − market, the disagreement the filter uses


def test_card_prints_the_matchup_and_model_numbers() -> None:
    spans = card_lines(_card())
    joined = "\n".join(_text_at(spans, r) for r in range(ROWS))
    assert "GEORGIA TECH" in joined
    assert "GEORGIA" in joined
    assert "14.8 - 37.9" in joined
    assert "UGA -23.0" in joined  # model's own spread
    assert "10,000 GAMES" in joined
    assert "91%" in joined and "9%" in joined


def test_market_fields_are_optional() -> None:
    """A board that hasn't posted yet still renders — the market block is empty."""
    spans = card_lines(_card(market_spread=None, market_total=None))
    joined = "\n".join(_text_at(spans, r) for r in range(ROWS))
    assert "O/U" not in joined
    assert "14.8 - 37.9" in joined  # the model half is unaffected
    assert not any(s.color == AMBER for s in spans)


def test_render_writes_the_fixed_frame(tmp_path: Path) -> None:
    path = render_terminal_card(_card(), tmp_path / "terminal.png")
    image = plt.imread(path)
    assert image.shape[0] == 900
    assert image.shape[1] == 1600


def test_render_survives_an_empty_distribution(tmp_path: Path) -> None:
    path = render_terminal_card(_card(total_pmf={}), tmp_path / "empty.png")
    assert plt.imread(path).shape[:2] == (900, 1600)


@pytest.mark.parametrize("league", ["NCAAF", "NFL"])
def test_league_names_the_card(league: str) -> None:
    spans = card_lines(_card(league=league))
    assert league in _text_at(spans, 0)


# --- block numerals + the hero layout ----------------------------------------


def test_block_text_is_five_rows_of_equal_width() -> None:
    from velocity.report.terminal_card import _GLYPH_ROWS, block_text, block_width

    rows = block_text("91%")
    assert len(rows) == _GLYPH_ROWS
    assert len({len(r) for r in rows}) == 1, "block rows must be rectangular"
    assert len(rows[0]) == block_width("91%")
    # Glyphs vary in width — a period is one cell, a percent five.
    assert block_width(".") == 1
    assert block_width("%") == 5
    assert block_width("") == 0


def test_block_text_falls_back_on_unknown_glyphs() -> None:
    from velocity.report.terminal_card import block_text

    rows = block_text("Z")  # not in the font
    assert len(rows) == 5
    assert set("".join(rows)) <= {" "}  # blank, not an exception


def test_hero_layout_prints_the_headline_numbers() -> None:
    from velocity.report.terminal_card import block_text, hero_card_lines

    spans = hero_card_lines(_card())
    joined = "\n".join(_text_at(spans, r) for r in range(ROWS))
    # The favourite's win probability, the model total, and the market gap are
    # each drawn as block numerals rather than plain text.
    for value in ("91%", "53.0", "+4.0"):
        first_row = block_text(value)[0]
        assert any(first_row in _text_at(spans, r) for r in range(ROWS)), value
    assert "UGA WIN" in joined
    assert "MODEL TOTAL" in joined
    assert "VS MARKET 49.0" in joined


def test_hero_layout_stays_inside_the_frame() -> None:
    from velocity.report.terminal_card import hero_card_lines

    for card in (_card(), _card(p_home_win=0.5, fair_total=100.0, market_total=99.5)):
        for span in hero_card_lines(card):
            assert 0 <= span.row < ROWS
            assert span.col + len(span.text) <= COLS, f"'{span.text}' overruns"


def test_hero_layout_without_a_market_drops_the_gap() -> None:
    from velocity.report.terminal_card import hero_card_lines

    spans = hero_card_lines(_card(market_spread=None, market_total=None))
    joined = "\n".join(_text_at(spans, r) for r in range(ROWS))
    assert "VS MARKET" not in joined
    assert "MODEL TOTAL" in joined  # the model's own headline is unaffected
    assert not any(s.color == AMBER for s in spans)


def test_both_styles_render(tmp_path: Path) -> None:
    for style in ("hero", "compact"):
        path = render_terminal_card(_card(), tmp_path / f"{style}.png", style=style)
        assert plt.imread(path).shape[:2] == (900, 1600)
