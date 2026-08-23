```sql game
select away_team || ' @ ' || home_team as matchup, kickoff, upper(league) as lg
from velocity.games
where game_id = '${params.game_id}'
limit 1
```

# {game[0]?.matchup ?? 'Matchup'}

```sql proj
select mu_away, mu_home, p_home_win, fair_spread, fair_total, n_sims
from velocity.projections
where game_id = '${params.game_id}'
limit 1
```

<BigValue data={proj} value=mu_away title="Projected away" fmt='#,##0.0' />
<BigValue data={proj} value=mu_home title="Projected home" fmt='#,##0.0' />
<BigValue data={proj} value=p_home_win title="Home win %" fmt='pct1' />
<BigValue data={proj} value=fair_total title="Fair total" fmt='#,##0.0' />

```sql conditions
select w.covered, w.temp_f, w.wind_mph, w.precip_pct
from velocity.weather w
where w.game_id = '${params.game_id}'
limit 1
```

<WeatherLine row={conditions[0]} />

## Line movement

```sql moves
select
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'Moneyline' else market end as market_label,
  upper(side) as side,
  point_open, price_open, point_now, price_now,
  case when market != 'moneyline' then point_now - point_open end as pt_move
from velocity.line_moves
where game_id = '${params.game_id}'
order by market, side
```

<DataTable data={moves} emptySet=pass emptyMessage="Movement appears once the hourly odds archive has seen this game more than once.">
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=point_open title="Open" fmt='#,##0.0' />
  <Column id=price_open title="Open price" fmt='+#,##0;-#,##0' />
  <Column id=point_now title="Now" fmt='#,##0.0' />
  <Column id=price_now title="Now price" fmt='+#,##0;-#,##0' />
  <Column id=pt_move title="Move" fmt='+#,##0.0;-#,##0.0' contentType=delta />
</DataTable>

## Markets

```sql markets
select
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'Moneyline'
    when 'team_total_home' then 'Team total (home)'
    when 'team_total_away' then 'Team total (away)'
    else market end as market_label,
  upper(side) as side, point, book, price, p_model, p_fair, edge,
  coalesce(tier, '') as tier, stake
from velocity.board
where game_id = '${params.game_id}'
order by edge desc
```

<DataTable data={markets} emptySet=pass emptyMessage="No priced markets for this game.">
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=point title="Line" fmt='#,##0.0' />
  <Column id=book title="Book" />
  <Column id=price title="Price" fmt='+#,##0;-#,##0' />
  <Column id=p_model title="Model %" fmt='pct1' />
  <Column id=p_fair title="Fair %" fmt='pct1' />
  <Column id=edge title="Edge" fmt='pct1' contentType=delta />
  <Column id=tier title="Tier" />
</DataTable>

## Simulated total

```sql total_pmf
select value, prob
from velocity.distributions
where game_id = '${params.game_id}' and kind = 'total'
order by value
```

<BarChart
  data={total_pmf}
  x=value
  y=prob
  xAxisTitle="total points"
  yAxisTitle="probability"
  emptySet=pass
  emptyMessage="No distribution banked for this game."
/>

## Simulated margin

```sql margin_pmf
select value, prob
from velocity.distributions
where game_id = '${params.game_id}' and kind = 'margin'
order by value
```

<BarChart
  data={margin_pmf}
  x=value
  y=prob
  xAxisTitle="home margin"
  yAxisTitle="probability"
  emptySet=pass
  emptyMessage="No distribution banked for this game."
/>

## Injury report

```sql inj
select i.team, i.player_name, i.position, i.status, i.is_out
from velocity.injuries i
join velocity.projections p
  on p.game_id = '${params.game_id}'
 and (i.team = p.away or i.team = p.home)
where i.league != '__none__'
order by i.team, i.is_out desc, i.player_name
```

<DataTable data={inj} rows=30 groupBy=team emptySet=pass emptyMessage="No banked injury designations for these teams (the daily snapshot covers NFL).">
  <Column id=player_name title="Player" />
  <Column id=position title="Pos" />
  <Column id=status title="Status" />
</DataTable>

## Cards

The rendered graphics for this game — tap to open full-size, save to post.

```sql game_cards
select kind, league, file, away, home, caption
from velocity.cards
where league != '__none__' and game_id = '${params.game_id}'
order by case kind when 'sheet' then 0 when 'social' then 1
  when 'deepdive' then 2 else 3 end
```

<CardGallery cards={game_cards} empty="No cards rendered for this game yet — they publish with each run." />
