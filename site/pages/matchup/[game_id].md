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
  <Column id=price title="Price" fmt='+#,##0' />
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
