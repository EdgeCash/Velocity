"""The market monitor on a synthetic season chain with a known drifting market."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.report.monitor import health_lines, lower_tail_p, market_health

AS_OF = pd.Timestamp("2026-10-30")


def _rows(  # noqa: PLR0913 - a synthetic market has this many dials
    market: str, days: range, per_day: int, *, clv: float, p_model: float, win_rate: float,
    seed: int, stake: float = 1.0, price: float = -110.0,
) -> list[dict[str, object]]:
    rng = np.random.default_rng(seed)
    rows = []
    for day in days:
        date = AS_OF - pd.Timedelta(days=day)
        for _ in range(per_day):
            won = rng.random() < win_rate
            rows.append({
                "section": "games", "play": f"{market}-{day}", "market": market,
                "side": "home", "point": -3.0, "price": price, "stake": stake,
                "result": "win" if won else "loss",
                "profit": stake * 100 / 110 if won else -stake,
                "line_clv": clv + rng.normal(0.0, 0.3),
                "price_clv": None, "p_model": p_model, "slate_date": date,
            })
    return rows


def _chain() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    # Spreads: beat the close by +0.4 for weeks, then lose to it by a point
    # over the last week — the 7-day window must flag, the 30-day must not.
    rows += _rows("spread", range(7, 40), 4, clv=0.4, p_model=0.54, win_rate=0.55, seed=1)
    rows += _rows("spread", range(0, 7), 3, clv=-1.0, p_model=0.54, win_rate=0.50, seed=2)
    # Totals: fine throughout, and calibrated.
    rows += _rows("total", range(0, 40), 3, clv=0.3, p_model=0.55, win_rate=0.55, seed=3)
    # Receptions (a prop — no close worth reading): loses steadily while the
    # model claims 0.62 — the total_bases pattern.
    rows += _rows("receptions", range(0, 40), 2, clv=0.0, p_model=0.62, win_rate=0.40, seed=4)
    # A market with three bets: thin, whatever it did.
    rows += _rows("team_total_home", range(0, 3), 1, clv=-2.0, p_model=0.6, win_rate=0.0,
                  seed=5)
    # Paper rows (stake 0) and pending rows never move a market's money.
    rows.append({"section": "games", "play": "paper", "market": "total", "side": "over",
                 "point": 44.5, "price": -110.0, "stake": 0.0, "result": "win", "profit": 0.0,
                 "line_clv": 0.5, "price_clv": None, "p_model": 0.6, "slate_date": AS_OF})
    rows.append({"section": "games", "play": "pending", "market": "total", "side": "over",
                 "point": 44.5, "price": -110.0, "stake": 1.0, "result": "pending",
                 "profit": None, "line_clv": None, "price_clv": None, "p_model": 0.6,
                 "slate_date": AS_OF})
    return pd.DataFrame(rows)


def test_drifting_spread_flags_in_the_short_window_only() -> None:
    table = market_health(_chain(), as_of=AS_OF).set_index(["market", "window_days"])
    short = table.loc[("spread", 7)]
    long = table.loc[("spread", 30)]
    assert short["n_bets"] == 21 and long["n_bets"] == 21 + 4 * 23
    assert short["mean_line_clv"] < -0.7 and long["mean_line_clv"] > 0
    assert bool(short["flag_negative_clv"]) is True
    assert "negative CLV" in short["flags"]
    assert bool(long["flag_negative_clv"]) is False
    assert long["flags"] == ""
    # Totals stay clean in both windows.
    assert table.loc[("total", 7), "flags"] == ""
    assert table.loc[("total", 30), "flags"] == ""


def test_losing_prop_is_an_exclusion_candidate_and_overclaims() -> None:
    table = market_health(_chain(), as_of=AS_OF).set_index(["market", "window_days"])
    long = table.loc[("receptions", 30)]
    assert bool(long["clv_trusted"]) is False
    assert long["roi"] < 0
    assert bool(long["flag_negative_roi"]) is True
    assert bool(long["flag_exclusion"]) is True
    assert bool(long["flag_overclaims"]) is True
    assert long["claimed"] == pytest.approx(0.62)
    assert long["drift"] < -0.05
    assert "exclusion candidate" in long["flags"] and "overclaims" in long["flags"]
    # The short window carries the ROI read but never names an exclusion:
    # that is the long window's call.
    short = table.loc[("receptions", 7)]
    assert bool(short["flag_exclusion"]) is False
    # An untrusted market never gets a CLV flag, whatever its CLV.
    assert bool(long["flag_negative_clv"]) is False


def test_thin_markets_paper_and_pending_rows() -> None:
    table = market_health(_chain(), as_of=AS_OF).set_index(["market", "window_days"])
    thin = table.loc[("team_total_home", 30)]
    assert bool(thin["thin"]) is True and thin["flags"] == "thin (3 bets)"
    assert bool(thin["flag_negative_clv"]) is False
    # The paper total counted no money and the pending one nothing at all.
    total = table.loc[("total", 30)]
    assert total["n_bets"] == 91  # 90 staked + the paper row
    assert total["staked"] == pytest.approx(90.0)


def test_empty_and_dateless_chains_are_clean() -> None:
    assert market_health(None, as_of=AS_OF).empty
    assert market_health(pd.DataFrame(), as_of=AS_OF).empty
    only_pending = pd.DataFrame([{"market": "total", "result": "pending", "stake": 1.0,
                                  "profit": None, "slate_date": AS_OF}])
    assert market_health(only_pending, as_of=AS_OF).empty
    # A chain from before the window: nothing settled inside it.
    old = _chain().assign(slate_date=AS_OF - pd.Timedelta(days=100))
    assert market_health(old, as_of=AS_OF).empty
    assert health_lines(pd.DataFrame(), "nfl") == [
        "NFL market health: nothing settled in the window"]


def test_health_lines_put_flagged_markets_first() -> None:
    lines = health_lines(market_health(_chain(), as_of=AS_OF), "nfl")
    assert lines[0] == "NFL market health, trailing 30 days:"
    assert "receptions" in lines[1] and "⚑" in lines[1]
    assert "spread" in " ".join(lines)


def test_lower_tail_p() -> None:
    assert lower_tail_p(np.array([1.0])) != lower_tail_p(np.array([1.0]))  # nan
    assert lower_tail_p(np.array([-1.0, -1.0, -1.0])) == 0.0
    assert lower_tail_p(np.array([1.0, 1.0])) == 1.0
    losing = lower_tail_p(np.array([-1.0, -0.8, -1.2, -0.9, -1.1] * 6))
    winning = lower_tail_p(np.array([1.0, 0.8, 1.2, 0.9, 1.1] * 6))
    assert losing < 0.001 and winning > 0.999
