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

```sql publish
select game_id, market, side, player, published, reason, tier, league
from velocity.publish
where league != '__none__'
```

```sql distributions
select game_id, kind, value, prob, league
from velocity.distributions
where league != '__none__'
```

```sql teams
select league, team, code, color, color_dark, logo
from velocity.teams
where league != '__none__'
```

```sql weather
select game_id, league, covered, temp_f, wind_mph, precip_pct
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
  ledgerOpen={ledger_open}
  bankroll={bankroll}
  exposure={exposure}
  units={units}
  record={record}
  clv={clv}
  modelConfig={model_config}
  stamp={meta[0]?.stamp ?? ''}
  tier={meta[0]?.tier ?? 'private'}
/>
