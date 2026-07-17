"""Point-in-time guards — the anti-lookahead contract.

If any of these fail, the model can see the future. That is the highest-value
class of test in the whole suite.
"""

from __future__ import annotations

import pandas as pd

from velocity.store.pit import available_as_of, closing_line, lines_before_kickoff


def test_available_as_of_is_strict_by_default(lines: pd.DataFrame) -> None:
    cut = pd.Timestamp("2023-09-07 18:00:00")
    out = available_as_of(lines, cut, ts_col="timestamp")
    # L2/L4 stamped exactly at the cut must be excluded when strict.
    assert (out["timestamp"] < cut).all()
    assert "L1" in set(out["line_id"])
    assert "L2" not in set(out["line_id"])


def test_available_as_of_inclusive(lines: pd.DataFrame) -> None:
    cut = pd.Timestamp("2023-09-07 18:00:00")
    out = available_as_of(lines, cut, ts_col="timestamp", inclusive=True)
    assert "L2" in set(out["line_id"])


def test_lines_before_kickoff_drops_post_kickoff(
    lines: pd.DataFrame, games: pd.DataFrame
) -> None:
    pre = lines_before_kickoff(lines, games)
    # L3 is stamped after the 20:20 kickoff even though it is flagged is_closing.
    assert "L3" not in set(pre["line_id"])
    assert "L1" in set(pre["line_id"])


def test_closing_line_is_last_pre_kickoff_not_the_flag(
    lines: pd.DataFrame, games: pd.DataFrame
) -> None:
    close = closing_line(lines, games)
    spread = close[(close["market"] == "spread") & (close["side"] == "home")]
    assert len(spread) == 1
    # The honest close is L2 (18:00, 3.5), NOT the flagged-but-post-kickoff L3.
    assert spread["line_id"].iloc[0] == "L2"
    assert float(spread["point"].iloc[0]) == 3.5
