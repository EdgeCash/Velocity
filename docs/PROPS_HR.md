# The home-run vertical

The lottery-ticket prop, modeled honestly — and the reason it is worth
modeling at all.

## 1. Why this is not purely luck

A single home run is close to pure noise: a rare binary event a bettor
cannot feel an edge on. The *rate* is a different animal. Statcast measures
the swing that produces home runs — exit velocity, launch angle, the
"barrel" combination of the two — rather than the rare outcome itself, so it
stabilizes far sooner than the counting stat.

Measured on our own pull (Savant leaderboards joined to statsapi HR totals,
2025 → 2026, 213 batters with volume in both seasons):

| Predictor of the NEXT season's HR/PA | r² |
| --- | --- |
| prior-season barrel rate | 0.39 |
| prior-season xISO | 0.39 |
| prior-season HR/PA (the counting stat) | 0.36 |
| prior-season average exit velocity | 0.26 |

Within a season the association is much stronger still (xISO r² = 0.72,
barrel rate 0.66), though that is partly circular — the same batted balls
that became home runs feed the expected metric.

The edge claim is narrow and specific: **batted-ball quality predicts future
home runs slightly better than past home runs do**, and the market anchors
on the counting stat. Everything here is built on that gap.

## 2. The obstacle is the vig, not the variance

Anytime-home-run markets carry far heavier hold than sides and totals. A
model can be genuinely sharper than the book and still lose to the toll, so
the prop side of this vertical stays gated until we have measured the real
hold from our own banked `batter_home_runs` lines (`collect_football_props`
now snapshots them alongside pitcher Ks). No play is staked on the strength
of the model alone.

The **DK "Home Runs" single-stat contest** has no such toll. Verified
against the live lobby: `salaryCap.isEnabled = false`, ~292 draftables, a
three-player roster. There is no line to beat and no cap to optimize — it
reduces to ranking the field by expected home runs, which is exactly what
the model produces. That is where a lottery-ticket edge actually survives.

## 3. The model

`velocity/models/props_hr.py`. Every term is fit from banked data; nothing
is imported from a paper.

    P(HR per PA) = batter_rate × pitcher_factor × park_factor
    P(≥1 HR)     = 1 − (1 − p)^PA

* **batter_rate** — empirical Bayes with a *Statcast-informed* prior. The
  batter's observed HR/PA shrinks toward the rate his barrel rate implies
  (via a league-fit `hr_pa ~ barrel_rate` line), not toward the league mean.
  A rookie with elite contact quality is not priced as league average; a
  veteran outperforming his batted balls gets pulled back. Prior strength
  350 PA. Without a Statcast frame the prior collapses to the league rate
  and the model degrades gracefully to plain shrinkage.
* **pitcher_factor** — the opposing starter's HR allowed per batter faced,
  shrunk to league over a 400-BF prior, clamped to [0.65, 1.55].
* **park_factor** — each venue's HR/PA relative to league, shrunk over a
  6,000-PA prior, clamped to [0.80, 1.30].
* **expected PA** — fit per lineup slot from the bank (leadoff hitters bat
  more than the nine hole). Substitutes carry no projectable workload.

`expected_home_runs()` is the contest ranking statistic; `GameHRProps`
implements the same `PropDistributions` protocol the pitcher-K model does,
so it plugs into `build_prop_slate` unchanged.

## 4. Data

| Bank | Source | Notes |
| --- | --- | --- |
| `datasets/mlb/batters.parquet` | statsapi box scores | per-game PA / HR / lineup slot per batter; banked in the same pass as the starters frame |
| `datasets/mlb/starters.parquet` | statsapi box scores | HR allowed + batters faced per start |
| Statcast snapshot | Baseball Savant `csv=true` leaderboards | keyed by MLBAM player id — joins to the box-score banks with no name matching |

A note on the box-score `battingOrder`: the **team-level** list is the
*final* lineup, so a starter who was replaced is absent from it. The
player-level code (`"400"` = the starter in the 4th slot, `"401"` = his
replacement) is the only honest read of who actually started. Getting this
backwards silently mislabels every substituted starter.

## 5. Validation

`scripts/validate_hr_model.py` walks forward through the schedule: fit only
on games completed before each window, predict P(≥1 HR) for every batter who
started in it, then score against what happened — Brier against two
baselines (league base rate; the batter's own raw HR/PA, i.e. what the
market anchors on) plus a calibration table.

**Result — 2026 windows, fit only on completed games (34,889 predictions
across 148 slates, 11 fit windows):**

| | Brier ↓ | AUC ↑ | top-3 per slate ↑ |
| --- | --- | --- | --- |
| league base rate | 0.101482 | 0.500 | — |
| batter raw HR/PA (what the market anchors on) | 0.101265 | 0.5999 | 0.1914 |
| **model** | **0.100426** | **0.6054** | **0.2455** |

The headline is the contest column. Ranking each slate and taking three
bats, the model's picks homer **24.6%** of the time against a **11.5%**
field average — 2.1× — and **+5.4 points** over ranking by the counting
stat alone. Calibration holds across all eight buckets (largest gap 1.6
points, slightly over-confident at the very top):

| predicted | realized | n |
| --- | --- | --- |
| 0.059 | 0.059 | 4,361 |
| 0.093 | 0.091 | 4,361 |
| 0.118 | 0.120 | 4,361 |
| 0.154 | 0.144 | 4,361 |
| 0.200 | 0.184 | 4,362 |

Brier barely moves (+0.83%) because the event is rare enough that the base
rate dominates the score — which is exactly why the gate reads AUC and
per-slate top-k rather than Brier alone.

**Two validation traps this harness had to fix, both worth remembering:**

1. *Baseline leakage.* The first run handed the baselines each batter's
   **realized** plate appearances while the model got only a projection. A
   batter who came up five times homers more often, so the baseline was
   quietly reading the outcome — it flipped the verdict from +0.83% to
   −0.24%. Both sides now get the same projected PA.
2. *Pooled top-k.* Ranking across all 34,889 predictions at once answers no
   real question. The contest is per-day, so top-k is computed within each
   slate and averaged over days.

**On the Statcast prior:** with prior-season barrel rates only (the clean
backtest — the leaderboards are season-cumulative, so current-season figures
would leak), the prior is a wash: AUC 0.6074 vs 0.6054, but slightly worse
Brier and top-3, and worse calibration in the bottom bucket. Once a batter
has real in-season plate appearances his own HR rate outruns last year's
batted balls. So the prior ships **off by default**, with the hook kept for
the early-season window where in-season volume is thin — the case the
backtest cannot measure, because Savant publishes no as-of snapshots.

## 6. Open items

* **Weather.** Temperature and wind genuinely move home-run distance, but we
  have no banked historical weather to FIT a coefficient on, so the model
  states nothing about it rather than importing someone else's number.
  Backfilling game-time conditions onto the batter bank is the unlock.
* **Handedness splits.** Batter-vs-LHP/RHP and handedness-specific park
  factors are the largest missing context term.
* **Confirmed lineups.** The board currently projects each batter's most
  recent starting slot; the official card lands ~3 hours pre-game.
* **Bullpen exposure.** Only the starter's HR-allowed rate is priced today.
