"""Odds adapters — a swappable interface over line providers.

The projection layer must not care where a price came from, so the odds provider
is a **swappable adapter** (DESIGN §2): backtests read historical closing lines
from a free archive; production reads a paid multi-book feed. Both satisfy the
same :class:`OddsAdapter` interface and return frames on the canonical
:class:`~velocity.store.schema.Lines` schema, so swapping free → paid is a config
change, not a rewrite.

Because both implementations produce identical canonical frames from the same
underlying observations, a **parity** check (the paid adapter matches the free
one on overlapping history) is just an equality assertion on their output.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd

from velocity.store.schema import Lines
from velocity.wagering.odds import american_to_decimal

_LINES_COLUMNS = [
    "line_id",
    "game_id",
    "book",
    "market",
    "side",
    "price",
    "point",
    "timestamp",
    "is_closing",
]


@runtime_checkable
class OddsAdapter(Protocol):
    """The contract every line provider satisfies.

    ``current_lines`` returns a canonical ``Lines`` frame, optionally filtered to
    ``game_ids``. Implementations differ only in where the rows come from.
    """

    name: str

    def current_lines(self, game_ids: Iterable[str] | None = None) -> pd.DataFrame: ...


def normalize_odds(raw: pd.DataFrame) -> pd.DataFrame:
    """Coerce a provider frame with the canonical columns onto the ``Lines`` schema."""
    missing = [c for c in _LINES_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"odds frame missing required columns: {missing}")
    out = raw[_LINES_COLUMNS].copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    return Lines.validate(out.reset_index(drop=True))


@dataclass
class HistoricalOddsAdapter:
    """Free adapter: serves lines from a stored archive (the backtest source)."""

    archive: pd.DataFrame
    name: str = "historical"

    def current_lines(self, game_ids: Iterable[str] | None = None) -> pd.DataFrame:
        df = self.archive
        if game_ids is not None:
            df = df[df["game_id"].isin(set(game_ids))]
        return normalize_odds(df)


@dataclass
class LiveOddsAdapter:
    """Paid/live adapter: serves lines from an injected fetch callable.

    The ``fetch`` callable is where a real provider client (e.g. The Odds API)
    plugs in; it returns a frame with the canonical columns, which is then
    validated. Injecting it keeps this class free of any network dependency and
    makes the contract testable offline.
    """

    fetch: Callable[[Iterable[str] | None], pd.DataFrame]
    name: str = "live"

    def current_lines(self, game_ids: Iterable[str] | None = None) -> pd.DataFrame:
        return normalize_odds(self.fetch(game_ids))


def shop_best_prices(lines: pd.DataFrame) -> pd.DataFrame:
    """Reduce a multi-book frame to the best price per (game, market, side, point).

    Line shopping is a real, free edge — the same bet at a better number pays
    more — so across all books we keep the single most favorable price for each
    distinct bet.
    """
    if lines.empty:
        return lines.reset_index(drop=True)
    df = lines.copy()
    df["_decimal"] = df["price"].map(american_to_decimal)
    keys = ["game_id", "market", "side", "point"]
    idx = df.groupby(keys, dropna=False)["_decimal"].idxmax()
    best = df.loc[idx].drop(columns="_decimal")
    return best.sort_values("line_id").reset_index(drop=True)
