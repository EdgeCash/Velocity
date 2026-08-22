# Velocity — NCAAB Vertical: Phased Build Plan

**Status:** Phase N1 landed (data adapter); N2+ planned
**Why this sport:** the edge research ([`EDGE_RESEARCH.md`](EDGE_RESEARCH.md)
§3.1) — inefficiency concentrates where pregame information is scarce, and
NCAAB is 360 teams × 5,000+ games with documented historical anomalies
(home/big underdogs, unders on high totals, early-season/low-major softness)
while the tournament itself tests efficient. It is the NCAAF thesis with a
winter schedule, and it reuses the stack: ridge-adjusted efficiency →
Monte Carlo → EV gate → Kelly → CLV.

**Expectation discipline up front:** kenpom/Torvik-class ratings are the
market's own inputs — the raw model should roughly *tie* the close. The edge
layers are what those ratings omit: injuries/rosters (kenpom is
roster-blind), per-venue home court (2.5–6+ points of spread across
venues, altitude at the extreme), first-half/derivative pricing, and
November/low-major selectivity. 53–55% ATS long-run is a strong result;
any internal backtest above ~56% at volume is presumed leaky.

---

## Phase N1 — Data (landed with this PR)

- **`velocity/ingest/ncaab.py`** — Torvik team-results normalizer covering
  both wire shapes (header-keyed CSV, positional JSON) → the canonical
  ratings frame (`team/conf/rank/adj_o/adj_d/adj_t/barthag/wab`);
  `expected_matchup` gives the possession-decomposition prior
  (pace × efficiency with a flat HCA split); `TorvikClient` fetches live
  and **timemachine** (as-of-date) payloads.
- **`scripts/collect_torvik.py`** — banks either mode as parquet.
- The timemachine archive is the point: daily as-of-date ratings are the
  leak-free walk-forward input, for free. Odds for backtests come from the
  free sportsbookreviewsonline archives (open + close, ~2007+).
- Author etiquette: contact before bulk scraping; the collector fetches one
  payload per invocation.

## Phase N2 — Model

Ridge-adjusted possession efficiency fit from game results (the
`fit_scores_ratings` pattern, per-100-possession units), blended with the
Torvik prior early-season and phased out as games accumulate (the priors
machinery in `features/priors.py`). Sim: score = pace × efficiency with
basketball variance constants calibrated to historical residuals. Edge
layers, in evidence order: per-venue HCA (estimate from historical margins,
altitude flagged), rest/travel interactions, and derivative (1H, team
total) pricing off the same sim. Gate: walk-forward Brier vs the
close-implied baseline, exactly the MODEL_LAB discipline.

## Phase N3 — Backtest

Walk-forward over the timemachine archive + sportsbookreviewsonline closes:
CLV and ATS by segment (November vs conference play, majors vs low-majors,
sides vs totals vs 1H). The tier backtest (`backtest/intel_tiers.py`)
replays here unchanged. Promotion bar: sustained segment-level edge with
per-season robustness, FDR-controlled across the sweep family
(`eval.metrics.benjamini_hochberg`).

## Phase N4 — Live

The `run_live_slate.py` path with a `ncaab` league key (The Odds API serves
`basketball_ncaab`), the injuries/roster layer wired through the intel
signals, and November/low-major selectivity as the launch filter. Softest
documented windows: early season (lines anchored to priors) and the
day-of injury/lineup news cycle.
