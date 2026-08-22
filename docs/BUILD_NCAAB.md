# Velocity — NCAAB Vertical: Phased Build Plan

**Status:** Complete — N1 (data adapter), N2 (model), N3 (closes backtest —
**null after FDR**), N4 (live, in the posture N3 dictates) all landed
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

**The gate** (`model_lab.py --league ncaab --eval-from 2026`, n=5,531
scored games, moneyline Brier — no closes joined yet, that's N3):

| variant | Brier | log-loss | calibration error |
|---------|-------|----------|-------------------|
| **prior-k6** (λ=0.5) | **0.1804** | **0.5304** | 0.0257 |
| prior-k3 | 0.1805 | 0.5309 | 0.0254 |
| prior-k12 | 0.1816 | 0.5336 | 0.0259 |
| ridge-0.25 | 0.1849 | 0.5456 | 0.0261 |
| prior-k24 | 0.1847 | 0.5414 | 0.0250 |
| pace-eff-flat (λ=0.5) | 0.1855 | 0.5493 | 0.0316 |
| pace-eff (λ=0.5, hl=6) | 0.1859 | 0.5504 | 0.0355 |
| ridge-0.1 | 0.1862 | 0.5479 | 0.0302 |
| ridge-1 | 0.1895 | 0.5621 | 0.0468 |
| ridge-2 | 0.1958 | 0.5797 | 0.0724 |

The full-season read reverses the mid-season wash: with November in the
denominator, **the prior variants sweep the top of the table** (k6 beats
the no-prior default by 5.2 Brier points per thousand and the best
no-prior ridge by 4.5), with the flat k3–k6 optimum saying a light prior
carries the early weeks and hands off cleanly. **Promoted N2 config:
pace×efficiency, λ=0.5, half-life 6, Torvik pseudo-games K=6** — the
`prior-k6` lab variant. N3 re-arbitrates against real closes (Brier vs
the close-implied baseline, CLV, and the segment cuts), where 0.1804 on
moneyline-from-ratings means nothing until it prices against the market.

## Phase N3 — Backtest (landed; the answer is null)

Walk-forward over the sportsbookreviewsonline closes: ATS/O-U by segment
(November vs conference play, majors vs low-majors), FDR-controlled across
the sweep family. Promotion bar: sustained segment-level edge. **Nothing
cleared it.**

### N3 results (2009–2022 walk-forward vs sbro closes)

**Market.** `velocity/ingest/sbro.py` + `scripts/collect_sbro_ncaab.py`
bank the free sbro archives (2007-08 through 2021-22; xlsx plus the final
season's HTML table): paired V/H rows, the multiplexed Open/Close cells
(the smaller number is the spread, on the favorite's row), pk/NL
sentinels. Names resolve to hoopR locations by compaction + a hand-checked
alias table (99.99% of ~125k name instances); games join per season on
(ET date, home, away) with swapped-order and ±1-day fallbacks, ambiguous
ids dropped and finals cross-checked against hoopR (99.8% agreement) —
`datasets/ncaab/closes.parquet`, 61,847 games with closes (98.8% of the
archive), home ATS 47–51% per decided season, matching the documented
profile. The games/box/Torvik frames extend back to 2008 to cover it.

**The gate** (`model_lab.py --league ncaab --eval-from 2009 --eval-to 2022
--train-window 5`, 80,364 scored games):

| variant | Brier | flat ATS | flat O/U | spread @≥6 pts | 
|---------|-------|----------|----------|----------------|
| pace-eff (no prior) | 0.1708 | 49.9% (49,944) | 50.1% (52,921) | 52.0% (4,308) |
| **prior-k6** | **0.1702** | 50.1% (49,650) | 50.4% (52,923) | 52.4% (3,365) |
| prior-k12 | 0.1714 | 50.4% (50,090) | 50.5% (52,958) | 52.9% (4,152) |

- **The expectation discipline held exactly**: Torvik-class ratings are
  the market's own inputs, and the raw model ties the close flat (~50%
  both markets, all variants). prior-k6's Brier edge from N2 replicates
  at 80k games.
- **The close is the better forecaster**: the market-blend sweep picks
  w=0.0 on the select window (2009–2017) and the holdout agrees (market
  0.1791 vs best model 0.1831) — no residual win-prob information on top
  of the closing spread.
- **Selectivity climbs but never clears the vig**: disagreement sweeps
  rise monotonically (prior variants uniformly above no-prior), reaching
  52.4–52.9% at ≥6 points — break-even at −110, on ~4k bets.
- **Segments: 0 of 90 cells survive FDR**
  (`ncaab_segment_study` × 3 variants → `benjamini_hochberg`, α=0.05).
  Best cell: prior-k12 spreads in conference play at ≥4 pts, 52.48% on
  6,185 bets (p=0.45 vs break-even). November totals lean over (51.5–52%)
  — the documented direction, not significance. The 2008-era anomalies
  (home dogs, low-major softness) are visibly arbitraged out by the later
  seasons of this window.

**What this dictates for N4**: launch as a content/CLV surface, not a
staking surface — project every game, grade CLV against live closes, keep
the EV gate on posted prices (where the devig + disagreement machinery
prices real numbers, not synthetic −110), and bank live closes from The
Odds API to re-test the 2023+ market regime the free archive doesn't
cover. The one hypothesis N3 licenses for a future sweep round: the
prior-weight direction is monotone in the sweeps (k12 > k6 > none on
selectivity), so a heavier/timemachine-refreshed prior belongs in the
next family — as a pre-registered variant, not a cherry-pick.

## Phase N4 — Live (landed)

The `run_live_slate.py` path with the `ncaab` league key (The Odds API's
`basketball_ncaab`), deployed in the posture N3's null dictates — a
content + CLV surface on real posted prices, not a staking surface with a
claimed edge:

- **Model**: the promoted N2 configuration — pace×efficiency, λ=0.5,
  recency-6, Torvik pseudo-games prior at K=6 — fit live from the
  committed `datasets/ncaab` frames, falling back to the recency scores
  fit if the box/Torvik inputs are missing. Sim constants from the N2
  walk-forward residuals (sd_margin 13.0, sd_total 18.5).
- **Names**: provider nicknames ("Duke Blue Devils") bridge onto the
  hoopR-keyed model ("Duke") by the same prefix rule NCAAF uses
  (`nickname_aliases`); unresolved games are skipped and reported, never
  guessed.
- **Pricing**: the standard devig + EV gate on posted prices — real
  numbers with real vig, not the synthetic −110 the backtest graded. No
  NCAAB-specific selectivity filter ships: N3 licensed none.
- **Grading + CLV**: `grade_yesterday.py` fetches finals from the hoopR
  raw-CDN schedule (the CI-safe transport), with the nickname bridge now
  applied to the finals join for both college leagues;
  `collect-odds.yml` snapshots `basketball_ncaab` game lines on the
  hourly schedule into the private CLV archive — the live closes that
  will re-test the 2023+ market regime the free sbro archive doesn't
  cover.
- **Cards**: matchup/deep-dive cards render with neutral trigram identity
  (no curated color/abbreviation table yet). The injuries/roster intel
  channel stays dormant until an NCAAB injury source is banked — none of
  the current collectors covers college basketball.
