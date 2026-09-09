"""Cross-venue closing-line comparison — is the exchange sharper than the book?

This is the question the exchange build exists to answer
(docs/BUILD_EXCHANGES.md E7). The closing line is the sharpest widely available
forecast, so comparing each venue's close against realised outcomes says which
venue is worth originating into: a venue whose closes are *softer* is where a
model's edge survives, and one whose closes are sharper is a better benchmark
than a bet.

Two rules keep the comparison honest:

* **Like-for-like prices only.** An executable ask and a mid/last sample are
  not the same number — comparing them is biased by roughly half the spread,
  and the bias differs by venue, which would masquerade as a sharpness gap.
  Rows carry a ``price_basis`` and mixed bases are excluded, not silently
  averaged.
* **Vig and fees removed.** A sportsbook's margin is in its price and an
  exchange's spread is in its two asks, so raw implied probabilities are not
  comparable at all. Both are de-vigged to a fair probability first.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from velocity.eval.metrics import brier_score
from velocity.store.schema import contract_key
from velocity.wagering.devig import devig

_OPPOSITE = {"home": "away", "away": "home", "over": "under", "under": "over"}


def fair_closing_probabilities(closes: pd.DataFrame) -> pd.DataFrame:
    """De-vig each venue's closing pair into a fair probability per side.

    ``closes`` is a closing board (one row per game/market/side/book, and per
    point for a ladder venue). Each side is paired with its opposite in the
    same contract at the same book; a side with no opposite is dropped, since
    a one-sided quote carries no removable margin.
    """
    if closes.empty:
        return closes.assign(p_fair=pd.Series(dtype=float))

    work = closes.copy()
    # Pair on the contract, not the raw number: a spread's two sides carry
    # mirrored points and would otherwise never meet.
    work["_point"] = [
        contract_key(str(m), str(s), None if pd.isna(p) else float(p))
        for m, s, p in zip(work["market"], work["side"], work["point"], strict=True)
    ]
    work["_point"] = work["_point"].fillna(-9999.0)
    rows: list[dict[str, object]] = []
    keys = ["game_id", "market", "book", "_point"]
    for (game_id, market, book, _contract), group in work.groupby(keys):
        prices = dict(zip(group["side"], group["price"], strict=False))
        bases = set(
            group.get("price_basis", pd.Series(["unknown"] * len(group))).fillna("unknown")
        )
        # One value per contract, or "mixed" — computed once, because the loop
        # below emits a row per side and must not consume it.
        basis = bases.pop() if len(bases) == 1 else "mixed"
        for side, price in prices.items():
            opposite = _OPPOSITE.get(str(side))
            if opposite is None or opposite not in prices:
                continue
            fair = devig([float(price), float(prices[opposite])])
            rows.append(
                {
                    "game_id": game_id,
                    "market": market,
                    "book": book,
                    "side": side,
                    # Report the row's own signed point, not the normalized key.
                    "point": None if pd.isna(group["point"].iloc[0]) else float(
                        group.loc[group["side"] == side, "point"].iloc[0]
                    ),
                    "p_fair": fair[0],
                    "price_basis": basis,
                }
            )
    return pd.DataFrame(rows)


def venue_sharpness(
    fair_closes: pd.DataFrame,
    outcomes: pd.Series,
    *,
    require_basis: str | None = "ask",
) -> pd.DataFrame:
    """Brier score of each venue's fair closing probability against outcomes.

    ``outcomes`` is indexed like ``fair_closes`` and holds 1 where the side
    won. A **lower** Brier is a sharper close. ``require_basis`` drops rows
    whose price basis differs from the comparison's, because a mid-quoted
    close and an ask-quoted one are not the same measurement; pass ``None``
    only when every row is known to share a basis.
    """
    work = fair_closes.assign(_won=pd.Series(outcomes).astype(float))
    if require_basis is not None and "price_basis" in work.columns:
        work = work[work["price_basis"] == require_basis]
    work = work.dropna(subset=["p_fair", "_won"])
    if work.empty:
        return pd.DataFrame(columns=["book", "n", "brier", "mean_p_fair"])

    rows = []
    for book, group in work.groupby("book"):
        rows.append(
            {
                "book": book,
                "n": int(len(group)),
                "brier": brier_score(
                    np.asarray(group["p_fair"], dtype=float),
                    np.asarray(group["_won"], dtype=float),
                ),
                "mean_p_fair": float(group["p_fair"].mean()),
            }
        )
    return pd.DataFrame(rows).sort_values("brier").reset_index(drop=True)


def compare_closes(
    book_closes: pd.DataFrame,
    exchange_closes: pd.DataFrame,
    on: tuple[str, ...] = ("game_id", "market", "side", "point"),
) -> pd.DataFrame:
    """Side-by-side fair closing probabilities for the same outcome.

    Joins a sportsbook's fair close to an exchange's on the same contract and
    reports the gap. A positive ``disagreement`` means the exchange closed the
    side higher — which is where an origination edge would have to live.
    """
    if book_closes.empty or exchange_closes.empty:
        return pd.DataFrame(columns=[*on, "p_book", "p_exchange", "disagreement"])

    left = book_closes.rename(columns={"p_fair": "p_book", "book": "sportsbook"})
    right = exchange_closes.rename(columns={"p_fair": "p_exchange", "book": "venue"})
    merged = left.merge(right, on=list(on), how="inner", suffixes=("_book", "_exchange"))
    if merged.empty:
        return merged.assign(disagreement=pd.Series(dtype=float))
    merged["disagreement"] = merged["p_exchange"] - merged["p_book"]
    return merged
