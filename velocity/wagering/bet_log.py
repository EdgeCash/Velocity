"""Bet log, closing-line value, and settlement.

Every bet is recorded with the price and number we took *and* the closing line,
because **closing-line value (CLV)** — did we beat the final pre-game price? — is
the leading indicator of genuine edge. A model can run hot or cold over a few
hundred bets by luck; consistent positive CLV is the durable signal.

Two complementary CLV measures are tracked:

* **Price CLV** — how much better our odds were than the close, as a fraction:
  ``entry_decimal / closing_decimal − 1``. Positive means we locked in a better
  price than the market settled at.
* **Line CLV (points)** — for spreads and totals, how many points better our
  *number* was, signed so positive is always in our favor (a home spread bettor
  wants more points; an over bettor wants a lower total). Half a point through a
  key number is a real, bookable edge, so the number matters as much as the juice.

Settlement grades each bet against the final score and rolls a bankroll curve.
Grading is a pure function of the ticket and the score, so the whole curve is
reproducible.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

import pandas as pd

from velocity.wagering.odds import american_to_decimal, net_payout

# Point-based markets and the sign of "a higher stored number helps this side".
# Stored ``point`` is always from the side's own perspective (added to its
# score for spreads; the over/under threshold for totals).
_SPREAD_SIDES = {"home", "away"}
_TOTAL_SIDES = {"over", "under"}


@dataclass(frozen=True)
class Bet:
    """A single wager ticket plus the closing line, for CLV."""

    game_id: str
    market: str
    side: str
    book: str
    price: float
    stake: float
    p_model: float
    point: float | None = None
    timestamp: pd.Timestamp | None = None
    closing_price: float | None = None
    closing_point: float | None = None
    player: str | None = None  # set for player props; None for game markets
    p_fair: float | None = None  # de-vigged market probability at entry (edge = p_model - p_fair)

    @property
    def net_payout(self) -> float:
        """Profit per unit staked on a win (decimal − 1)."""
        return net_payout(self.price)

    def price_clv(self) -> float | None:
        """Fractional price edge over the close, or ``None`` if no close is known."""
        if self.closing_price is None:
            return None
        return american_to_decimal(self.price) / american_to_decimal(self.closing_price) - 1.0

    def line_clv(self) -> float | None:
        """Signed points beaten vs the closing number (positive = in our favor).

        ``None`` for moneylines (no point) or when no closing point is known.
        """
        if self.point is None or self.closing_point is None:
            return None
        if self.market == "spread":
            # Higher handicap always helps the side holding it.
            return self.point - self.closing_point
        if self.side in _TOTAL_SIDES:
            # Over/under lines (game totals and player props): over wants a lower
            # number, under wants a higher one.
            return (self.closing_point - self.point) if self.side == "over" else (
                self.point - self.closing_point
            )
        return None

    def grade(self, home_score: float, away_score: float) -> tuple[str, float]:
        """Return ``(result, profit)`` for this bet given the final score.

        ``result`` is one of ``"win"``, ``"loss"`` or ``"push"``; ``profit`` is
        in stake units (``+stake·b`` on a win, ``−stake`` on a loss, 0 on a push).
        """
        margin = home_score - away_score
        total = home_score + away_score

        if self.market == "moneyline":
            edge = margin if self.side == "home" else -margin
        elif self.market == "spread":
            if self.side not in _SPREAD_SIDES:
                raise ValueError(f"invalid spread side {self.side!r}")
            if self.point is None:
                raise ValueError("spread bet requires a point")
            edge = (margin + self.point) if self.side == "home" else (-margin + self.point)
        elif self.market == "total":
            if self.side not in _TOTAL_SIDES:
                raise ValueError(f"invalid total side {self.side!r}")
            if self.point is None:
                raise ValueError("total bet requires a point")
            edge = (total - self.point) if self.side == "over" else (self.point - total)
        else:
            raise ValueError(f"unknown market {self.market!r}")

        if edge > 0:
            return "win", self.stake * self.net_payout
        if edge < 0:
            return "loss", -self.stake
        return "push", 0.0


class BetLog:
    """An ordered collection of bets with CLV and settlement reporting."""

    def __init__(self) -> None:
        self._bets: list[Bet] = []

    def add(self, bet: Bet) -> None:
        self._bets.append(bet)

    def __len__(self) -> int:
        return len(self._bets)

    def __iter__(self) -> Iterator[Bet]:
        return iter(self._bets)

    @property
    def bets(self) -> tuple[Bet, ...]:
        return tuple(self._bets)

    def clv_summary(self) -> dict[str, float]:
        """Aggregate CLV: mean price CLV, mean line CLV, and % of bets with +CLV.

        The positive-CLV rate uses price CLV where available and falls back to
        line CLV, so every bet with any closing information counts.
        """
        price: list[float] = [p for b in self._bets if (p := b.price_clv()) is not None]
        line: list[float] = [ln for b in self._bets if (ln := b.line_clv()) is not None]
        beat: list[float] = []
        for b in self._bets:
            value = b.price_clv()
            if value is None:
                value = b.line_clv()
            if value is not None:
                beat.append(value)
        n_beat = sum(1 for v in beat if v > 0)
        return {
            "n_bets": float(len(self._bets)),
            "mean_price_clv": float(sum(price) / len(price)) if price else float("nan"),
            "mean_line_clv": float(sum(line) / len(line)) if line else float("nan"),
            "pct_positive_clv": float(n_beat / len(beat)) if beat else float("nan"),
        }

    def settle(self, games: pd.DataFrame, starting_bankroll: float = 100.0) -> pd.DataFrame:
        """Grade every bet against final scores and roll the bankroll forward.

        Returns one row per bet (in bet order) with the result, profit, running
        bankroll, and both CLV measures. Bets on unplayed games (null scores) are
        marked ``"pending"`` and leave the bankroll unchanged.
        """
        scores = {
            gid: (h, a)
            for gid, h, a in zip(
                games["game_id"], games["home_score"], games["away_score"], strict=True
            )
        }
        rows: list[dict[str, object]] = []
        bankroll = starting_bankroll
        for bet in self._bets:
            home_raw, away_raw = scores[bet.game_id]
            home = float(home_raw)
            away = float(away_raw)
            if math.isnan(home) or math.isnan(away):
                result, profit = "pending", float("nan")
            else:
                result, profit = bet.grade(home, away)
                bankroll += profit
            rows.append(
                {
                    "game_id": bet.game_id,
                    "market": bet.market,
                    "side": bet.side,
                    "book": bet.book,
                    "price": bet.price,
                    "point": bet.point,
                    "stake": bet.stake,
                    "p_model": bet.p_model,
                    "result": result,
                    "profit": profit,
                    "bankroll": bankroll,
                    "price_clv": bet.price_clv(),
                    "line_clv": bet.line_clv(),
                }
            )
        return pd.DataFrame(rows)
