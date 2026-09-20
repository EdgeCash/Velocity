---
title: Velocity
hide_title: true
sidebar: never
---

<!--
  The whole site.

  There is one page on purpose. Everything that used to be a tab — the board,
  the DFS slate, the props, the record, the per-game dossiers — is a view on
  this surface now, switched client-side and addressed by the URL hash. The
  queries below are the entire data contract; the app is in
  components/hub/.

  Every query filters the sentinel league. An empty source still writes one
  typed row (build_site_data.py) so the SQL parses on a quiet slate, and that
  row must never reach the surface as a game.
-->

```sql meta
select tier, stamp, built_at from velocity.site_meta
```

```sql games
select game_id, home_team, away_team, kickoff, league, stamp
from velocity.games
where league != '__none__'
```

```sql projections
select game_id, away, home, n_sims, mu_away, mu_home, p_home_win,
  fair_spread, fair_total, league, stamp
from velocity.projections
where league != '__none__'
```

```sql board
select
  game_id, market, side, point, book, price, p_model, p_fair, edge,
  stake_sized, note, tier, conviction, rationale, venue, venue_key,
  venue_label, league, stamp
from velocity.board
where league != '__none__'
```

<!-- The gate's verdicts, in full. This once selected only the columns the
     game-row count needed, and the Card view then rendered an em-dash for
     every price, edge, conviction and stake on it — the data was simply
     never queried. If the card grows a field, it has to be added here. -->
```sql publish
select game_id, market, side, player, price, stake, stake_sized, edge, tier,
  conviction, drift, context, published, reason, league, stamp
from velocity.publish
where league != '__none__'
```

```sql distributions
select game_id, kind, value, prob, league
from velocity.distributions
where league != '__none__'
```

```sql teams
select league, team, code, color, color_ui, logo
from velocity.teams
where league != '__none__'
```

<!-- Two wind numbers on purpose (docs/FOOTBALL_PAL.md): `wind_mph` is the
     kickoff-hour forecast a reader wants for conditions, `wind_model_mph` the
     daily max the Round-5 adjustment was fitted on and priced from. -->
```sql weather
select game_id, league, covered, temp_f, wind_mph, precip_pct,
  wind_model_mph, precip_in, wind_points, precip_points, total_points
from velocity.weather
where league != '__none__'
```

<!-- Named `player_props`, not `props`: Evidence generates a `let props`
     binding in the compiled page, and a query of that name collides with it —
     "Identifier 'props' has already been declared", at build time only. -->
```sql player_props
select game_id, player, market, side, point, book, price, p_model, p_fair,
  edge, stake, note, league, stamp
from velocity.props
where league != '__none__'
```

```sql line_moves
select game_id, market, side, point_open, price_open, point_now, price_now,
  seen_open, seen_now, league
from velocity.line_moves
where league != '__none__'
```

```sql injuries
select player_name, team, position, status, is_out, league
from velocity.injuries
where league != '__none__'
```

```sql dfs_lineup
select slot, player_name, position, team, salary, points, kickoff,
  slate_start, suffix, slate, game_time, league, stamp
from velocity.dfs_lineup
where league != '__none__'
```

```sql dfs_showdown
select slot, player_name, position, team, salary, points, kickoff,
  slate_start, suffix, slate, format, game_time, league, stamp
from velocity.dfs_showdown
where league != '__none__'
```

```sql dfs_tiered
select slot, player_name, position, team, tier, points, kickoff,
  format, game_type, unit, game_time, league, stamp
from velocity.dfs_tiered
where league != '__none__'
```

<!-- Every priced player, not only the rostered ones (docs/FOOTBALL_PAL.md). -->
```sql dfs_pool
select player_name, position, team, salary, points, value, rostered,
  competition, kickoff, status, probable, slate, game_time, league, stamp
from velocity.dfs_pool
where league != '__none__'
```

```sql ledger_open
select bet_id, league, kind, game_id, market, side, player, point, book,
  price, stake, home_team, away_team, placed_at
from velocity.ledger_open
where league != '__none__'
```

```sql bankroll
select seed, current, peak, drawdown, open_exposure, open_bets,
  settled_bets, halted, halt_threshold, mode, staked, profit, league
from velocity.bankroll
where league != '__none__'
```

```sql exposure
select league, bets, games, stake_sized, stake_solo, bankroll,
  cap_fraction, cap_units, exposure
from velocity.exposure
where league != '__none__'
```

```sql units
select league, slate_date, profit, profit_sized, bets, units, units_sized
from velocity.units
where league != '__none__'
```

```sql record
select section, play, market, side, point, price, stake, result, profit,
  price_clv, line_clv, stake_sized, profit_sized, close_source,
  slate_date, league
from velocity.cumulative_record
where league != '__none__'
```

```sql clv
select market, n_bets, mean_price_clv, mean_line_clv, pct_beat_close,
  clv_trusted, league, units
from velocity.clv_by_market
where league != '__none__'
```

```sql clv_tier
select rule_tier, n_bets, n_decided, win_rate, roi, mean_price_clv,
  mean_line_clv, pct_beat_close, league
from velocity.clv_by_tier
where league != '__none__'
```

```sql health
select market, window_days, since, n_bets, n_decided, staked, profit, roi,
  clv_trusted, n_clv, mean_line_clv, mean_price_clv, pct_beat_close,
  claimed, realized, drift, flag_negative_clv, flag_negative_roi,
  flag_overclaims, flag_exclusion, thin, flags, league, as_of
from velocity.market_health
where league != '__none__'
```

<!-- The matchup splits (docs/FOOTBALL_PAL.md): descriptive, not priced. -->
```sql unit_splits
select league, season_from, season_to, games, team, side, phase, plays,
  epa_per_play, epa_adjusted, success_rate
from velocity.unit_splits
where league != '__none__'
```

<!-- Player ratings (docs/FOOTBALL_PAL.md): season-wide, not board-scoped. -->
```sql player_ratings
select league, season_from, season_to, player_id, player, team, position,
  games, dropbacks, epa_per_dropback, cpoe, carries, rush_yards,
  yards_per_carry, targets, receptions, rec_yards, yards_per_target,
  dk_points_per_game
from velocity.player_ratings
where league != '__none__'
```

```sql ratings
select team, off, "def", net, pace, scale, rank, rank_prev, net_prev,
  league, stamp
from velocity.ratings
where league != '__none__'
```

```sql cards
select kind, league, stamp, file, away, home, caption, game_id
from velocity.cards
where league != '__none__'
```

```sql parlays
select legs, n_legs, price, decimal, p_win, ev, same_game, stake,
  legs_json, league, stamp
from velocity.parlays
where league != '__none__'
```

<!-- The accuracy chain (docs/FOOTBALL_PAL.md): what the model said before
     each graded game, what happened, and the percentile the final sat at on
     the pregame distribution. Model output and finals only. -->
```sql accuracy
select game_id, league, game_date, away_name, home_name, away_code, home_code,
  mu_away, mu_home, fair_spread, fair_total, p_home_win, away_score, home_score,
  actual_total, total_percentile, winner_code, winner_percentile,
  p_winner_pregame, n_sims, graded_stamp
from velocity.accuracy
where league != '__none__'
```

```sql model_config
select league, label, detail from velocity.model_config
where league != '__none__'
```

<Hub
  games={games}
  projections={projections}
  board={board}
  publish={publish}
  distributions={distributions}
  teams={teams}
  weather={weather}
  playerProps={player_props}
  lineMoves={line_moves}
  injuries={injuries}
  dfsLineup={dfs_lineup}
  dfsShowdown={dfs_showdown}
  dfsTiered={dfs_tiered}
  dfsPool={dfs_pool}
  ledgerOpen={ledger_open}
  bankroll={bankroll}
  exposure={exposure}
  units={units}
  record={record}
  clv={clv}
  modelConfig={model_config}
  health={health}
  ratings={ratings}
  unitSplits={unit_splits}
  playerRatings={player_ratings}
  cards={cards}
  parlays={parlays}
  accuracy={accuracy}
  stamp={meta[0]?.stamp ?? ''}
  builtAt={meta[0]?.built_at ?? ''}
  tier={meta[0]?.tier ?? 'private'}
/>
