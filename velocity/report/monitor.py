"""The market monitor — per-market trailing CLV and ROI, with flags.

The daily grade settles yesterday's plays into the season chain; this reads
that chain back as the operator's one-glance health table (docs/WAGERING.md
W3). For every market, over trailing 7- and 30-day windows: how many bets,
the return on the stakes, the two CLV measures and the share of bets that
beat the close, what the model *claimed* against what it *realized*, and a
set of flags:

* **negative CLV** — a market whose close is a yardstick (spreads, totals,
  moneylines) losing to it. The test is one-sided on the mean line CLV
  (price CLV where no line exists), and the flag is confirmed only where it
  survives Benjamini–Hochberg across the window's markets — the monitor
  will flag markets by chance otherwise (the §3 multiple-comparisons rule).
  A raw rejection that does not survive is reported as *unconfirmed*.
* **negative ROI** — realized return below zero with the same one-sided
  test on per-bet return; the read for markets whose close is *not* a
  yardstick (props, team totals), where P/L is all there is.
* **overclaims** — the model's mean claimed probability on decided bets
  runs ahead of the realized win rate by more than ``max_drift``: the
  shrink or the anchoring weight has drifted from what the record earns.
* **exclusion candidate** — an untrusted market with a confirmed negative
  ROI over the long window: what ``total_bases`` looked like before it was
  excluded. Exclusion itself still needs the flag to persist across two
  review windows; the monitor names candidates, the operator decides.
* **thin** — too few bets in the window to say anything; every other flag
  is suppressed.

Everything is a pure function of the record frame, so a synthetic chain
with a known drifting market tests the whole thing.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from velocity.eval.metrics import CLV_TRUSTED_MARKETS, benjamini_hochberg

WINDOWS = (7, 30)
MIN_BETS = 20
ALPHA = 0.10
MAX_DRIFT = 0.05
SETTLED = ("win", "loss", "push", "tie")

HEALTH_COLUMNS = [
    "market", "window_days", "since", "n_bets", "n_decided", "staked", "profit", "roi",
    "roi_p", "clv_trusted", "n_clv", "mean_line_clv", "mean_price_clv", "pct_beat_close",
    "clv_p", "claimed", "realized", "drift", "flag_negative_clv", "flag_negative_roi",
    "flag_overclaims", "flag_exclusion", "thin", "flags",
]


def lower_tail_p(values: np.ndarray) -> float:
    """One-sided p-value that the mean of ``values`` is below zero.

    A normal approximation to the t-test — fine at the window sizes the
    monitor reads (``MIN_BETS`` and up), and it keeps the report free of a
    scipy dependency. ``nan`` with fewer than two finite values or no
    spread at all.
    """
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return float("nan")
    sd = float(arr.std(ddof=1))
    if sd == 0.0:
        return 0.0 if float(arr.mean()) < 0 else 1.0
    t = float(arr.mean()) / (sd / math.sqrt(arr.size))
    return 0.5 * math.erfc(-t / math.sqrt(2.0))


def _window(record: pd.DataFrame, as_of: pd.Timestamp, days: int) -> pd.DataFrame:
    dates = pd.to_datetime(record["slate_date"], errors="coerce")
    start = as_of.normalize() - pd.Timedelta(days=days - 1)
    return record[(dates >= start) & (dates <= as_of.normalize() + pd.Timedelta(days=1))]


def _market_row(  # noqa: PLR0913 - one row, many measures
    market: str, part: pd.DataFrame, days: int, *, min_bets: int, max_drift: float,
) -> dict[str, object]:
    profit = pd.to_numeric(part["profit"], errors="coerce").fillna(0.0)
    stake = pd.to_numeric(part["stake"], errors="coerce").fillna(0.0)
    staked = float(stake.sum())
    # Per-bet return on the stake: the ROI test's sample.
    returns = (profit / stake).where(stake > 0)
    blank = pd.Series(float("nan"), index=part.index, dtype=float)
    price = (pd.to_numeric(part["price_clv"], errors="coerce")
             if "price_clv" in part.columns else blank)
    line = (pd.to_numeric(part["line_clv"], errors="coerce")
            if "line_clv" in part.columns else blank)
    # Line CLV where the market has a number, price CLV for moneylines.
    clv = line.where(line.notna(), price)
    decided = part[part["result"].isin(["win", "loss"])]
    claimed = realized = drift = float("nan")
    if "p_model" in part.columns:
        p_model = pd.to_numeric(decided["p_model"], errors="coerce")
        known = decided[p_model.notna()]
        if len(known):
            claimed = float(p_model[p_model.notna()].mean())
            realized = float((known["result"] == "win").mean())
            drift = realized - claimed
    n = len(part)
    thin = n < min_bets
    dates = pd.to_datetime(part["slate_date"], errors="coerce").dropna()
    return {
        "market": market,
        "window_days": days,
        "since": None if dates.empty else dates.min().normalize(),
        "n_bets": n,
        "n_decided": len(decided),
        "staked": staked,
        "profit": float(profit.sum()),
        "roi": float(profit.sum() / staked) if staked > 0 else float("nan"),
        "roi_p": float("nan") if thin else lower_tail_p(returns.dropna().to_numpy()),
        "clv_trusted": market in CLV_TRUSTED_MARKETS,
        "n_clv": int(clv.notna().sum()),
        "mean_line_clv": float(line.mean()) if line.notna().any() else float("nan"),
        "mean_price_clv": float(price.mean()) if price.notna().any() else float("nan"),
        "pct_beat_close": float((clv.dropna() > 0).mean()) if clv.notna().any() else float("nan"),
        "clv_p": float("nan") if thin else lower_tail_p(clv.dropna().to_numpy()),
        "claimed": claimed,
        "realized": realized,
        "drift": drift,
        "thin": thin,
        "_drift_n": len(decided) if not math.isnan(drift) else 0,
        "_max_drift": max_drift,
    }


def market_health(  # noqa: PLR0913 - the monitor's knobs
    record: pd.DataFrame | None,
    *,
    as_of: object,
    windows: tuple[int, ...] = WINDOWS,
    min_bets: int = MIN_BETS,
    alpha: float = ALPHA,
    max_drift: float = MAX_DRIFT,
) -> pd.DataFrame:
    """The health table: one row per (market, window), flags attached.

    ``record`` is a season chain (``RECORD_COLUMNS`` + ``slate_date``);
    only settled rows with a stake count, so paper calls never move a
    market's ROI (they carry no money) — their CLV still counts, on the
    trusted markets, through a separate pass. A day with no bets, or an
    empty chain, returns an empty table with the right columns.
    """
    empty = pd.DataFrame(columns=HEALTH_COLUMNS)
    if record is None or record.empty or "market" not in record.columns:
        return empty
    if "slate_date" not in record.columns:
        return empty
    settled = record[record["result"].isin(SETTLED)]
    if settled.empty:
        return empty
    when = pd.Timestamp(as_of)  # type: ignore[arg-type]
    if when.tzinfo is not None:
        when = when.tz_convert("UTC").tz_localize(None)

    rows: list[dict[str, object]] = []
    for days in windows:
        part = _window(settled, when, days)
        if part.empty:
            continue
        window_rows = [
            _market_row(str(market), group, days, min_bets=min_bets, max_drift=max_drift)
            for market, group in part.groupby("market", sort=True)
        ]
        _flag(window_rows, days, alpha=alpha, long_window=max(windows))
        rows.extend(window_rows)
    if not rows:
        return empty
    table = pd.DataFrame(rows)
    table = table.drop(columns=[c for c in table.columns if c.startswith("_")])
    return table[HEALTH_COLUMNS].reset_index(drop=True)


def _flag(rows: list[dict[str, object]], days: int, *, alpha: float, long_window: int) -> None:
    """Attach the flags to one window's rows, BH-controlled across markets."""
    def survivors(key: str, eligible: list[bool]) -> list[bool]:
        idx = [i for i, ok in enumerate(eligible) if ok]
        out = [False] * len(rows)
        if not idx:
            return out
        p = np.array([float(rows[i][key]) for i in idx])  # type: ignore[arg-type]
        keep = benjamini_hochberg(np.where(np.isnan(p), 1.0, p), alpha)
        for i, k in zip(idx, keep, strict=True):
            out[i] = bool(k)
        return out

    def testable(row: dict[str, object], key: str) -> bool:
        value = row[key]
        return not bool(row["thin"]) and isinstance(value, float) and not math.isnan(value)

    clv_ok = survivors("clv_p", [testable(r, "clv_p") and bool(r["clv_trusted"]) for r in rows])
    roi_ok = survivors("roi_p", [testable(r, "roi_p") for r in rows])
    for row, clv_hit, roi_hit in zip(rows, clv_ok, roi_ok, strict=True):
        flags: list[str] = []
        thin = bool(row["thin"])
        row["flag_negative_clv"] = clv_hit
        row["flag_negative_roi"] = roi_hit
        drift = row["drift"]
        overclaims = (
            not thin
            and isinstance(drift, float) and not math.isnan(drift)
            and int(row["_drift_n"]) >= MIN_BETS  # type: ignore[call-overload]
            and drift < -float(row["_max_drift"])  # type: ignore[arg-type]
        )
        row["flag_overclaims"] = overclaims
        row["flag_exclusion"] = (
            roi_hit and not bool(row["clv_trusted"]) and days == long_window
        )
        if thin:
            flags.append(f"thin ({row['n_bets']} bets)")
        else:
            if clv_hit:
                flags.append("negative CLV")
            elif (bool(row["clv_trusted"]) and testable(row, "clv_p")
                  and float(row["clv_p"]) < alpha):  # type: ignore[arg-type]
                flags.append("negative CLV (unconfirmed)")
            if roi_hit:
                flags.append("negative ROI")
            if overclaims:
                flags.append(f"overclaims by {-float(drift):.2f}")  # type: ignore[arg-type]
            if row["flag_exclusion"]:
                flags.append("exclusion candidate")
        row["flags"] = "; ".join(flags)


def health_lines(table: pd.DataFrame, league: str | None = None) -> list[str]:
    """The log's read of the table: one line per market for the long window,
    flagged rows first."""
    if table is None or table.empty:
        return [f"{(league or '').upper()} market health: nothing settled in the window".strip()]
    days = int(table["window_days"].max())
    part = table[table["window_days"] == days].copy()
    part["_flagged"] = part["flags"].astype(str).str.len() > 0
    part = part.sort_values(["_flagged", "n_bets"], ascending=[False, False])
    lines = [f"{(league or '').upper()} market health, trailing {days} days:".strip()]
    for r in part.to_dict("records"):
        clv = ("" if not r["clv_trusted"] else
               f", line CLV {r['mean_line_clv']:+.2f}" if not pd.isna(r["mean_line_clv"])
               else f", price CLV {r['mean_price_clv']:+.3f}" if not pd.isna(r["mean_price_clv"])
               else ", no close")
        roi = "" if pd.isna(r["roi"]) else f", ROI {r['roi']:+.1%}"
        drift = "" if pd.isna(r["drift"]) else f", claimed {r['claimed']:.2f} → {r['realized']:.2f}"
        flag = f"  ⚑ {r['flags']}" if r["flags"] else ""
        lines.append(f"  {r['market']:<18} {r['n_bets']:>4} bets{roi}{clv}{drift}{flag}")
    return lines
