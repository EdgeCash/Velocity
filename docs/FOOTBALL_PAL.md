# Football Pal — the product map

**What this is.** A private, personal research site for football wagering:
Ballpark Pal's shape, for the NFL and college football, on Velocity's own
models. One sport, one simulation, every page derived from it. No billing,
no public product, no picks service — a place to do the research and read
the model's output in one surface.

**Where it lives.** The hub (`docs/SITE.md`): one page, the views switched
client-side, the joins done once in `site/components/hub/model.js`. The
Access-gated Cloudflare Worker stays the only host; the public-tier build
flag is untouched (the Accuracy view carries model output and finals only,
so it survives that tier whole).

**Why football only.** `docs/LAUNCH.md` "Football only" and
`docs/FOOTBALL_CUTOVER.md`. Six leagues made the operation decent at all of
them and great at none; the schedule, the collectors and the site now serve
the two football leagues, with every other league still graded until its
open ledger rows settle.

## Ballpark Pal's menu, for football

| Ballpark Pal | Football Pal view | State | Built from |
|---|---|---|---|
| **Today's Outlook** | | | |
| Park Factors | the game sheet's weather and venue line | in the sheet | `weather`, `games` (roof/surface) |
| Game Simulations | **Games** — projection, distributions, board, moves, injuries per game | shipped | `projections`, `distributions`, `board` |
| Today's Pitchers | QB1 and the injury list on the sheet | in the sheet; a QB view is next | ESPN depth chart, injuries |
| BvP Matchups · Matchup Machine | unit matchups (pass offense vs pass defense, rush, pace) | **next** | nflverse EPA splits, the ratings fit |
| **Odds & Probability** | | | |
| Most Likely | **Most likely** — the sim's surest outcomes by family, with the best price beside each | **shipped 2026-09-19** | `mostLikely(games)` over the collapsed board and the prop board |
| Odds Screen | the board on every game sheet, best price first, books and exchanges | shipped | `board` |
| Positive EV | **Card** — what cleared the publish gate, and what did not, with why | shipped | `publish`, `board` |
| PrizePicks & Underdog | **Players → Lines** — one row per player line, both sides, best quotes, DFS salary | **shipped 2026-09-19**; against sportsbooks (the pick'em feeds are blocked, `collect-prizepicks.yml`) | `playerBook(games)` over `props` and the pool |
| Parlay Calculator | the correlated parlays block on the game sheet | shipped; an interactive builder is next | `parlays` |
| **Daily Fantasy** | | | |
| Daily Fantasy Projections | **Players → Pool** — every priced draftable with salary, projection and value | **shipped 2026-09-19** | `dfs_pool`, the builder's persisted pool |
| Stacks · Lineups · Ballpark DFS | **DFS** — classic, showdown, tiered, the GPP portfolio | shipped | `dfs_*` |
| **Research Tools** | | | |
| Player Ratings | player EPA / CPOE / success ratings | **next** | `datasets/nfl/plays.parquet`, nflverse weekly |
| Year-Long Park Factors | venue and weather effects on totals | research | `docs/EDGE_RESEARCH.md` 2.1 (wind ≥ 15 mph) |
| Cheat Sheets · Sim Outliers | the Card's held verdicts and Most likely together answer both | shipped | |
| Export Center | every table is already a parquet under `/data/velocity/`; a links panel is next | partial | |
| **The Model** | | | |
| Methods · FAQ | `docs/MODEL_LAB.md`, `docs/BACKTEST_NFL.md`, `docs/BACKTEST_NCAAF.md`, the model-config rail block | docs | `model_config` |
| Accuracy | **Accuracy** — bias, error, calibration deciles, every graded game | **shipped 2026-09-19** | the accuracy chain (below) |
| Ball Flight · Park Impact | no football analogue worth building | — | |

The command bar groups the views the same way: **Outlook** (Games) ·
**Odds** (Card, Most likely, Positions) · **Fantasy** (DFS, Players) ·
**Research** (Ratings) · **Model** (Record, Accuracy).

## What shipped on 2026-09-19

**Most likely** (`LikelyPanel.svelte`, `mostLikely` in `model.js`). Every
collapsed board market and every collapsed prop line, ranked by the model's
probability within four families — winners, spreads, totals, players — with
the best price across venues, its implied probability, and the gap to the
de-vigged fair number beside each. It is deliberately not the Card: an 80%
favourite at −400 is 80% likely and no bet, and the row says both.

**Players** (`PlayersPanel.svelte`, `playerBook`, `playerPool`). Two tables
under one search box, because the question is about a player and which table
holds the answer is not something a reader should have to decide first.
**Lines** is the prop board turned around: one row per (player, stat, line)
with the over and under probabilities, the leaning side marked, and the best
quote on each side. **Pool** is every priced draftable on the slate with its
salary, the model's projected DK points, and value — points per $1,000,
DraftKings' own convention — with the optimizer's own picks marked in place,
so the lineup reads as a subset of the pool rather than a separate list.

The pool exists because the solve used to throw it away: `solve_slate` built
the priced board, rostered nine players from it and discarded the rest, so
the DFS column could only speak for a player who happened to make the
lineup. `LineupRun` now carries the pool, `pool_frame` shapes it (value,
`rostered`, one row per draftable), and the builder persists
`dfs_pool_<league>_<stamp>.parquet` — banked even when the slate does not
solve, since an infeasible board still knows what every player is worth.
On the public tier both `salary` and `value` blank: value beside points
gives the salary exactly, the same arithmetic that keeps `edge` off a public
board.

**Accuracy** (`AccuracyPanel.svelte`, `accuracySummary`). Built from a new
**accuracy chain**: after grading, `scripts/grade_yesterday.py` writes each
Sim Check as a row (`simcheck_<league>_<stamp>.parquet`) and the season of
them (`cumulative_simcheck_<league>_<stamp>.parquet`), merged from every
copy in the previous runs' artifacts with the newest grade of a game winning
(`velocity.report.sim_check.sim_check_frame` / `merge_sim_check_chain`).
`build_site_data.py` collects the chain as the `accuracy` table. The view
reports games graded, how often the sim's favourite won, the Brier score on
the home win probability, the total bias (sim − actual, points and percent
— Ballpark Pal's headline number), mean absolute total and margin error, the
share of finals inside the sim's middle 50%, a decile strip of where the
actual totals landed on their pregame distributions (flat is calibrated),
and every graded game newest first.

The chain carries forward through the previous-slates fetch (twelve runs);
parking it in R2 beside the record chain is the follow-up that makes it
survive a retention gap.

## Next, in order

1. **A Weather & venue view**, with the near-kickoff wind read
   (`docs/EDGE_RESEARCH.md` 2.1) as its reason to exist.
2. **Unit matchups** from the EPA splits the ratings fit already computes.
3. **Player ratings** from the play-by-play bank (EPA, CPOE, success).
4. **An export panel** listing the parquets.
5. **The drive-level simulation** — the one structural gap against Ballpark
   Pal's bottom-up sim: possessions and plays conditioned on unit strength,
   pace and situation, producing team scores and player stats from one
   draw. Unifies the game and prop sims, gives the model a per-game
   uncertainty it does not have today (`docs/EDGE_RESEARCH.md` 6.1), and is
   promoted only through the lab's walk-forward gate.
6. **Delete the non-football code behind a tag** once the MLB ledger rows
   settle (`docs/FOOTBALL_CUTOVER.md` §2).
