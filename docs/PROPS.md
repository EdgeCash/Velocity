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

## Open items

- NFL receptions distributional model (targets × catch rate NegBin — the
  generic engine in `velocity/models/props.py` is built for this; needs
  `targets` added to the nflverse weekly normalizer).
- NHL SOG after the season opens (skater `sog` is in every banked
  boxscore path already).
- NBA vertical (nba_api pipeline) → rebounds vs assists lab arbitration.
- Prop CLV: closes for props from the banked line archive (game-market
  CLV is already automated).
