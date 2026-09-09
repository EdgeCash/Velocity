# Prop Mastery — one headline prop per sport

The owner's X strategy: be known for ONE prop per sport, with the graded
record as the receipt. The picks (owner-researched, model-confirmed):

| Sport | Headline prop | Why it models | Status |
|---|---|---|---|
| MLB | **Pitcher strikeouts** | BF × K-rate × opponent-K decomposition; our own decommissioned backtest measured it the best MLB prop (ROI ≈ +3–4% at shrink 0.5; docs/WAGERING.md §74) | **Live** (this PR) |
| NFL | **Receptions** | Stable target shares, discrete counts, NegBin-friendly | Next (season start) |
| NHL | **Shots on goal** | TOI × shot-rate is the most stable NHL skater stat; boxscore `sog` banked per game | After puck drop (Oct) |
| NBA | **Rebounds** | Minutes × pace × position matchup; softer than points markets | Needs the NBA vertical first — the lab will arbitrate rebounds vs assists |

**Line banking started for all four** — the props collector
(`scripts/collect_football_props.py`, workflow "Collect player-prop
lines") snapshots each league's headline market twice daily
(`LEAGUE_PROP_MARKETS`: MLB `pitcher_strikeouts`, NHL
`player_shots_on_goal`, NBA `player_rebounds`, football's six-market
board unchanged), so the honest line-based backtests accumulate their
own archive from today.

## MLB pitcher Ks (live)

`velocity/models/props_mlb.py` — expected Ks = shrunken expected batters
faced × shrunken K/BF × the opposing lineup's K tendency, all from the
banked starters history (13.8k starts, 2024–). Distribution: negative
binomial with dispersion fit from the same history. Priced into
`slate_mlb_props_*.parquet` by `run_live_slate` (`_mlb_k_slate`) against
The Odds API's `pitcher_strikeouts` board, using statsapi probables;
graded next morning against the day's boxscores
(`grade_yesterday._grade_mlb_props`), joining the record chain and the
site like every other play.

Walk-forward validation vs actuals (2,932 out-of-sample 2026 starts,
refit every 15 days): MAE 1.72 Ks, bias −0.15, and **monotone,
conservatively-priced calibration** —

| model says over 4.5 | realized |
|---|---|
| <35% | 14% |
| 35–50% | 39% |
| 50–65% | 58% |
| >65% | **81%** |

The tails understate (the model claims less edge than it has), which is
the safe direction for staking. Launch posture: raw probabilities
(shrink 1.0); the per-market shrink sweep replays the banked slates once
graded days accumulate, exactly as football props are tuned.

## Football dispersion — fitted, not assumed (2026-09)

`FootballPropConfig` shipped with priors ("honest, not fitted"). The banked
`datasets/nfl/player_weeks.parquet` (112,450 player-weeks, 2020–2025) fits
them (`scripts/fit_prop_dispersion.py`, docs/SYSTEM_REVIEW.md §5.2). Every
number is a *within player-season* measurement — each player's own season
as the projection, the game-to-game spread around it as the outcome noise
the sim owes — decomposed the way the sim generates:

| quantity | was | fitted | how |
|---|---|---|---|
| pass-volume σ (team multiplier) | 0.18 | **0.118** | team receptions CV² − 1/μ (the Poisson part out) |
| rush-volume σ | 0.25 | **0.175** | team carries, the same way |
| receptions overdispersion φ (per player, net of the multiplier) | 0 | **WR 0.027 · TE 0.000 · RB 0.083** | (var − mean)/mean² − (e^{σ²} − 1) |
| per-catch yards sd (× √receptions) | 6.0 | **WR 10.57 · TE 7.92 · RB 7.56** | sd of (yards − receptions × season ypr)/√receptions |
| rushing CV (player noise, net of the multiplier) | 0.45 | **RB 0.72 · QB 0.87** | relative deviation sd, net of the rush σ |
| rushing shape | normal | **banked pool** (`datasets/nfl/prop_residuals.parquet`, 7,701 game residuals) | relative-deviation skew RB 1.55 / QB 1.42 |

What the fit changes on the board: receiving-yard distributions are ~15%
wider (the √receptions scaling was right; the per-catch constant was not —
with 10.6 the sim's receiving-yards CV of 0.68 for a 4-catch receiver
matches the measured 0.69), rushing distributions are 60% wider and
right-skewed (above 1.5× the mean the real frequency is 20.0%; a normal at
the fitted CV says 25.2%, a gamma 20.5%, and below 0.25× the mean the
gamma is too light — the pool matches both tails by construction), and the
receptions count carries a back's extra script variance. Wider is the
conservative direction for every alt-line and most main-line overs: the
old sim over-claimed edges on yardage props.

**A prior the data refused.** The shared pass-volume multiplier was meant to
move teammates together. On 525 top-receiver pairs the week-to-week
correlation of teammates' receiving yards is **−0.02** (receptions +0.02):
the passing pie is not a common multiplier, target share trades off. The
fitted σ implies ~0.035 between two receivers (the old prior implied
0.075); the QB-to-receiver correlation is structural (his yards are the sum
of theirs) and unchanged.

Not a backtest: the props line archive began this season, so the shrink
sweep that judges *confidence* has nothing to replay yet. These constants
set the *shape* the sweep will then tune, exactly as MLB's did.

**Prop closes are attached.** The grader reads the props collector's
snapshots (`artifacts/props`, fetched beside the odds archive), takes each
book's last pre-kickoff quote per (player, market, side), and reduces
across books to a median close; the graded prop rows carry `price_clv` /
`line_clv` like the game ledger's. `clv_trusted` stays False for props —
this is what the shrink sweep grades against, not the yardstick.

## Open items

- NFL receptions distributional model (targets × catch rate NegBin — the
  generic engine in `velocity/models/props.py` is built for this; needs
  `targets` added to the nflverse weekly normalizer).
- NHL SOG after the season opens (skater `sog` is in every banked
  boxscore path already).
- NBA vertical (nba_api pipeline) → rebounds vs assists lab arbitration.
- ~~Prop CLV: closes for props from the banked line archive~~ — attached
  (above); the sweep needs graded weeks to accumulate.
