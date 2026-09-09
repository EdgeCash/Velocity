"""Point-in-time access — the anti-lookahead guard.

The single most common way sports models secretly cheat is by using
information that would not have been available before kickoff. These helpers
make "as of time T" a first-class, testable operation so features can only
ever see the past.
"""

from __future__ import annotations

import pandas as pd

from velocity.store.schema import LADDER_BOOKS

# Sentinel standing in for a null point when grouping (pandas drops NaN keys).
_NO_POINT = -9999.0


def available_as_of(
    df: pd.DataFrame,
    asof: pd.Timestamp,
    ts_col: str = "timestamp",
    inclusive: bool = False,
) -> pd.DataFrame:
    """Return only the rows observable at ``asof``.

    By default the cut is strict (``timestamp < asof``): a line stamped at the
    exact kickoff is *not* pre-game information. Set ``inclusive=True`` to use
    ``<=`` when the anchor represents "known through T".
    """
    ts = pd.to_datetime(df[ts_col])
    mask = ts <= asof if inclusive else ts < asof
    return df.loc[mask].copy()


def lines_before_kickoff(lines: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Keep only line observations stamped strictly before their game's kickoff."""
    merged = lines.merge(games[["game_id", "kickoff"]], on="game_id", how="inner")
    ts = pd.to_datetime(merged["timestamp"])
    kickoff = pd.to_datetime(merged["kickoff"])
    keep = merged.loc[ts < kickoff]
    return keep.drop(columns=["kickoff"]).reset_index(drop=True)


def closing_line(lines: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """The last line seen before kickoff for each (game, market, side, book).

    This is the honest closing line — the final pre-game price we could
    actually have bet — rather than trusting a possibly-stale ``is_closing``
    flag.

    Rows from a :data:`~velocity.store.schema.LADDER_BOOKS` venue additionally
    key on ``point``, because there every number is its own contract quoted at
    the same moment: without it, one snapshot's ~25 rungs collapse to a single
    arbitrary survivor and 24 real closes are silently discarded. A
    sportsbook's rows are grouped exactly as before, so its close still tracks
    the main line wherever it moved to.
    """
    pre = lines_before_kickoff(lines, games)
    if pre.empty:
        return pre
    pre = pre.sort_values("timestamp")
    keys = ["game_id", "market", "side", "book"]
    is_ladder = pre["book"].astype(str).str.lower().isin(LADDER_BOOKS)
    parts = []
    if (~is_ladder).any():
        parts.append(pre[~is_ladder].groupby(keys, as_index=False).tail(1))
    if is_ladder.any():
        ladder = pre[is_ladder]
        # ``point`` is nullable (moneyline), and pandas drops NaN group keys —
        # fill so moneyline rungs are grouped rather than discarded.
        filled = ladder.assign(_point=ladder["point"].fillna(_NO_POINT))
        parts.append(
            filled.groupby([*keys, "_point"], as_index=False).tail(1).drop(columns="_point")
        )
    if not parts:
        return pre.iloc[0:0]
    return pd.concat(parts, ignore_index=True).reset_index(drop=True)
