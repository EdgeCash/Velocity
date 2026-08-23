---
title: Today
hide_title: true
---

<LiveTicker />

```sql tiles
select
  (select count(distinct game_id) from velocity.games
    where league != '__none__') as games_today,
  (select count(*) from velocity.board
    where league != '__none__' and edge >= 0.02) as plays,
  coalesce((select sum(profit) from velocity.units
    where league != '__none__'), 0) as season_units,
  (select max(stamp) from velocity.board
    where league != '__none__') as as_of
```

<HeroBand
  title="Today's board"
  subtitle="Every priced market, ranked by model edge. Rows link to the matchup page; cards for sharing live in Graphics."
  stamp={tiles[0]?.as_of}
/>

<BigValue data={tiles} value=games_today title="Games on the board" />
<BigValue data={tiles} value=plays title="Plays (edge ≥ 2%)" />
<BigValue data={tiles} value=season_units title="Season units" fmt='+#,##0.0"U"' />

```sql board_rows
select
  upper(league) as lg,
  away_team || ' @ ' || home_team as matchup,
  '/matchup/' || game_id as matchup_link,
  case market
    when 'spread' then 'Spread'
    when 'total' then 'Total'
    when 'moneyline' then 'ML'
    when 'team_total_home' then 'TT home'
    when 'team_total_away' then 'TT away'
    else market end as market_label,
  upper(side) as side,
  point,
  price,
  p_model,
  edge,
  coalesce(tier, '') as tier,
  kickoff
from velocity.board
where league != '__none__'
order by edge desc
```

<DataTable data={board_rows} link=matchup_link rows=40 emptySet=pass emptyMessage="No slate loaded — the board fills when the daily run publishes.">
  <Column id=lg title="League" />
  <Column id=matchup title="Matchup" />
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=point title="Line" fmt='#,##0.0' />
  <Column id=price title="Price" fmt='+#,##0;-#,##0' />
  <Column id=p_model title="Model %" fmt='pct1' />
  <Column id=edge title="Edge" fmt='pct1' contentType=delta />
  <Column id=tier title="Tier" />
</DataTable>

_Model output, graded in public. Edge = model probability minus the de-vigged
market probability; the [Performance](/performance) page carries the record._

<!-- Crawl seed: the matchup template route must have at least one
     discoverable instance for the static build, even on an empty board
     (offseason). The sentinel page renders its empty states. -->
<a href="/matchup/__none__" style="display:none" aria-hidden="true">.</a>
