# The NHL Build

The sixth vertical, on the phased pattern the other leagues proved
(docs/BUILD_NCAAB.md): data → walk-forward gate → live wiring, nothing
promoted without winning the lab first.

## H1 — Data (done)

All from the official NHL API (`api-web.nhle.com`, free, keyless; the
edge 403s default-python user agents, so the client sends a tool UA —
`velocity/ingest/hockey.py`):

- `datasets/nhl/games.parquet` — 2023–2025 seasons (start-year labeled),
  regular season + playoffs, 4,192 finals. 32 club-season schedule calls
  per season, deduped by game id. Extra column `last_period_type`
  (REG/OT/SO — 22% of games pass regulation). Week buckets are 15-day
  slices counted from Sep 15 of the start year (day-of-year would wrap
  at New Year), so the walk-forward slices stay date-monotone.
- `datasets/nhl/starters.parquet` — the STARTING goalie per side from
  each game's boxscore (`starter: true`, explicit), 8,384 rows = 100%
  coverage of finals, 126 goalies. `scripts/build_nhl_datasets.py`
  banks both, incrementally.

Empirical outcome noise (the sim calibration): margin sd **2.61**,
total sd **2.31**, mean total 6.17, home win 54.1%.

## H2 — The lab gate (done)

`model_lab.py --league nhl` — 4,165 out-of-sample games, walk-forward:

| variant | Brier | notes |
|---|---|---|
| **sp-q40 @ team λ=25 (goalie decomposition)** | **0.24258** | promoted |
| sp-q15 / sp-q80 @ λ=25 | 0.24268 / 0.24264 | interior optimum ≈ q40 |
| sp-q40 @ λ=100 (round 1) | 0.24308 | |
| ridge-25 (plain scores fit) | 0.24350 | best plain fit |
| scores λ=100 | 0.24419 | first-guess default |
| recency-2/4/8 | 0.2447–0.2468 | monotone worse — full history wins |

The MLB finding repeats in goals: decomposing the **starting goalie**
out of team defense (the same `mlb_starter_frame` → `fit_qb_ratings`
machinery, goalie = SP) beats every plain fit, with the goalie-dummy
shrinkage optimum at q≈15–40. Recency weighting hurts — NHL team
strength is stable within the three-season window.

Live pricing note: there is no free API for *confirmed* starting
goalies hours ahead (the NHL's own pregame feed lists candidates only),
so the live slate prices **goalie-neutral** — the decomposition still
earns its keep by cleaning the team estimates (goalie noise removed
from defense). Plugging in confirmed starters (DailyFaceoff) is a
follow-up that only adds signal.

## H3 — Live wiring (this PR)

- The Odds API `icehockey_nhl`; provider full names →
  NHL abbreviations via `NHL_TEAM_ALIASES` (both Utah identities).
- `run_live_slate --league nhl`: goalie-decomposed fit when
  `starters.parquet` is present (promoted config: team λ=25, q=40), scores-fit fallback; sim sds 2.6/2.3.
- Grading: NHL API `/v1/score/{date}` finals (±1 day window) in
  `grade_yesterday`; CLV closes from the hourly odds archive, which now
  snapshots NHL too.
- Cards: abbreviation + brand-color identity
  (`velocity/report/league_identity.py` — no marks, the licensing
  posture); sheets, sim checks, ratings, and the site's NHL card room
  all flow through the existing per-league machinery.

## Open items

- Confirmed starting goalies pregame (DailyFaceoff scrape) → plug into
  `StarterAwareModel`'s lookup for goalie-aware pricing.
- Shots-on-goal props (the owner's NHL headline prop): skater `sog` is
  in every boxscore; The Odds API carries `player_shots_on_goal`.
  Build after the MLB pitcher-Ks prop model proves the pattern.
- Closes-joined backtest (sbro NHL archives exist) for an N3-style
  honest edge assessment; until then the vertical runs in the same
  content+CLV posture NCAAB launched with.
