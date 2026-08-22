# Velocity — NCAAB Vertical: Phased Build Plan

**Status:** Phases N1 (data adapter) + N2 (model) landed; N3+ planned
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

## Phase N2 — Model (landed)

Ridge-adjusted possession efficiency fit from game results (the
`fit_scores_ratings` pattern, per-100-possession units), blended with the
Torvik prior early-season and phased out as games accumulate. Sim:
score = pace × efficiency with basketball variance constants calibrated to
historical residuals. Gate: walk-forward Brier, exactly the MODEL_LAB
discipline. Edge layers (per-venue HCA, rest/travel, derivatives) are N3+
work, in evidence order.

### N2 results (2026 walk-forward)

**Data.** `scripts/build_ncaab_datasets.py` banks
`datasets/ncaab/{games,team_box,torvik}.parquet` — 47,140 completed games /
94,194 box rows (hoopR raw-CDN parquets, the WNBA wehoop pattern; ESPN's
own edge 403s datacenter IPs) and 2,865 Torvik season-end rating rows,
2019–2026, ~1.5 MB total. Weeks are Nov-1-anchored 15-day buckets
(`ncaab_week`) — the day-of-year convention the daily leagues use inverts
across New Year. Every Torvik rating row 2019–2026 maps to a hoopR team
name (`torvik_team_candidates`: St.→State expansion + a hand-checked alias
table).

**The compression finding.** The reused pace×efficiency fit
(`fit_pace_efficiency`, half-life 6 buckets ≈ 90 days), walk-forward within
2026 at football-scale ridge λ=50, showed margin sd 16.06 and a −3.6 mean
"bias" on non-neutral games. Bucketing residuals by predicted margin showed
it was not a home-edge offset but **rating compression**: calibration slope
act ≈ 2.6·pred (a +6 projected favorite realized −2.5; a +14.5 realized
+19.6). 360 conference-clustered teams give the ridge graph weak
cross-cluster connectivity, and shrinkage flattens exactly those strength
differences. The slope walks monotonically to 1 as λ drops — what
predictions consume is each team's offense+defense *sum*, which stays
identified as λ→0:

| λ | margin sd | slope (act on pred) | margin bias |
|---|-----------|---------------------|-------------|
| 50 | 16.06 | 2.63 | −2.85 |
| 10 | 15.01 | 1.71 | −2.49 |
| 2 | 13.76 | 1.29 | −1.33 |
| 0.5 | 12.92 | 1.13 | −0.15 |
| 0.1 | 12.52 | 1.03 | +0.48 |

Total sd is flat ~18.5 throughout with bias ≤ +0.75 (the recency weighting
already absorbed the +5.6 cross-season environment drift a flat fit
shows). Shipped constants (`INSEASON_CALIBRATION["ncaab"]`): sd_margin
13.0, sd_total 18.5, ridge 0.5 — λ=0.5 concedes ~0.4 margin sd vs the
λ=0.1 floor for early-season stability; the lab ladder (0.1–2) keeps the
floor contested.

**The Torvik prior** enters as *pseudo-games*, not a rating blend:
`torvik_pseudo_games` emits K copies per team of a synthetic week-0
neutral game vs a shared `__PRIOR__` anchor, scores `adj_o·adj_t/100` /
`adj_d·adj_t/100` with `poss = adj_t`, so the per-100 conversion recovers
the ratings exactly and the anchor's own fitted rating re-centers Torvik's
scale. The existing ridge + recency machinery then decays the prior
naturally as real games arrive. Leak gate: a rating season enters only
once the training slice's latest kickoff reaches April 10 of its closing
year. Measured mid/late-season (the diagnostic slice trains on ≥300
current-season games), the prior is a wash at low ridge (K=12 λ=1: sd
13.33 vs 13.28 without) — as expected once compression is fixed at the
source; its case is the early weeks, which the lab's full-season
walk-forward measures.

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
