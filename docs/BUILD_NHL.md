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
from defense). ~~Plugging in confirmed starters (DailyFaceoff) is a
follow-up that only adds signal.~~

> **Read H4 before this table.** Every Brier above was measured with the
> evaluated game's *actual* goalie in the lookup; production ships an
> empty one, so **0.24258 is not the number that ships — 0.24299 is**.
> The ordering and the promotion survive (neutral still beats the best
> plain fit at 0.24350), but the margin over a plain fit is about half
> what this table implies. And "only adds signal" is wrong as stated: a
> *guessed* goalie measures worse than no goalie at all.

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

## H4 — The goalie-lookup measurement (2026-09-15)

H2 promoted the decomposition at Brier **0.24258** and separately noted that
live pricing is goalie-neutral. Both true; together they hid a third thing.
**The promoted number was measured with the evaluated game's actual goalie in
the lookup, and production ships an empty one** — so the configuration that
ships had never been the configuration that was measured. H2's open item then
proposed plugging a starter feed in on the reasoning that it "only adds
signal", which is the claim this tests.

`scripts/nhl_goalie_lab.py`, 3,966 out-of-sample games, five paired seeds
(same RNG stream per week, so the per-seed difference is the signal and its
spread across seeds is the noise):

| lookup | Brier | calib. err | vs neutral | sd | beat neutral |
|---|---|---|---|---|---|
| `neutral` — **what ships** | 0.242988 | 0.0199 | — | — | — |
| `depth-10` | 0.243039 | 0.0228 | **+0.000051** | 0.000020 | 0 of 5 |
| `depth-20` | 0.242964 | 0.0229 | −0.000025 | 0.000025 | 4 of 5 |
| `oracle` — actual starter | 0.242621 | 0.0199 | **−0.000367** | 0.000029 | 5 of 5 |

**A depth chart is not a goalie feed.** Over the banked seasons the nominal #1
— the goalie with the most starts in his team's trailing games — actually
starts **59–61%** of the time, and the window barely matters (59.0% at 5 games,
60.7% at 10, 59.0% at 41). That is not a stale depth chart; it is a genuine
rotation, and a #1 whose share of his own team's starts averages 61% (min 35%,
max 80%) is not a starter you can name in advance.

Priced, that guess is **worse than knowing nothing**: `depth-10` loses to
neutral in all five seeds, and both depth variants degrade calibration error by
about 15% (0.0199 → 0.0229) while the oracle leaves it untouched. That is the
mechanism in one number — a wrong goalie does not average out, it prices the
game as someone else.

**And the ceiling is small.** Perfect goalie knowledge is worth 0.00037 Brier,
about 0.15%. It is a real effect (5 of 5 seeds, ~12× its own spread) and it is
not worth a scrape: a *confirmed*-goalie feed, the H2 open item, is bounded
above by that number, and any real feed delivers less.

**Decisions.**

1. **Do not wire the ESPN depth chart into `StarterAwareModel`'s lookup.** It
   is available (`scripts/collect_espn_depth.py`, NHL included) and it measures
   worse than the empty lookup it would replace.
2. **Goalie-neutral stays** — now on evidence rather than on the absence of a
   feed.
3. **H2's decomposition survives intact.** Neutral (0.24299) still beats the
   best plain fit (ridge-25, 0.24350), so decomposing goalie noise out of team
   defense earns its keep exactly as H2 said. What changes is the *number*:
   0.24258 was the oracle's, and production's is 0.24299. The gap is the
   goalie knowledge production does not have.

Caveat this measurement does not escape: the NHL vertical has no closes-joined
archive, so this is Brier/log-loss/calibration only — no ROI, no CLV. A signal
worth 0.0004 Brier is well inside the range where the market would decide
whether it matters at all, and there is no market yardstick here to ask.

## Open items

- ~~Confirmed starting goalies pregame (DailyFaceoff scrape) → plug into
  `StarterAwareModel`'s lookup for goalie-aware pricing.~~ **Measured and
  dropped (H4).** The oracle ceiling is 0.00037 Brier; a depth chart lands
  the wrong side of neutral. Revisit only if the closes-joined backtest
  below shows the moneyline is priced tightly enough for 0.0004 to move
  money.
- Shots-on-goal props (the owner's NHL headline prop): skater `sog` is
  in every boxscore; The Odds API carries `player_shots_on_goal`.
  Build after the MLB pitcher-Ks prop model proves the pattern.
- Closes-joined backtest (sbro NHL archives exist) for an N3-style
  honest edge assessment; until then the vertical runs in the same
  content+CLV posture NCAAB launched with.
