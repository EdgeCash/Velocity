---
title: Today
hide_title: true
---

<LiveTicker />

```sql tiles
select
  (select count(distinct game_id) from velocity.games
    where league != '__none__') as games_today,
  (select sum(stake_sized) from velocity.exposure
    where league != '__none__') as sized_total,
  (select sum(cap_units) from velocity.exposure
    where league != '__none__') as cap_total,
  (select count(*) from velocity.board
    where league != '__none__' and stake_sized > 0) as staked,
  (select count(*) from velocity.board
    where league != '__none__' and note is not null) as paper,
  coalesce((select sum(profit_sized) from velocity.units
    where league != '__none__'), 0) as season_units,
  (select max(stamp) from velocity.board
    where league != '__none__') as as_of,
  (select strftime(max(seen_now), '%Y-%m-%d %H:%M') from velocity.line_moves
    where league != '__none__') as odds_as_of
```

```sql exposure_tile
select
  case when cap_total > 0 then sized_total / cap_total end as of_cap,
  coalesce(sized_total, 0) as sized_total,
  coalesce(cap_total, 0) as cap_total
from ${tiles}
```

<HeroBand
  title="Today's board"
  subtitle="Every priced market, ranked the way the card is built: tier, then conviction, then edge. Stake is the portfolio-sized number; paper rows are priced and graded but not wagered."
  stamp={tiles[0]?.as_of}
/>

<BigValue data={tiles} value=games_today title="Games on the board" />
<BigValue data={tiles} value=staked title="Staked plays" />
<BigValue data={exposure_tile} value=of_cap title="Exposure (sized / slate cap)" fmt='pct0' />
<BigValue data={tiles} value=season_units title="Season units (sized)" fmt='+#,##0.0"U"' />

```sql exposure_rows
select upper(league) as lg, bets, games, stake_sized, cap_units,
  exposure, stake_solo
from velocity.exposure
where league != '__none__'
order by stake_sized desc
```

<DataTable data={exposure_rows} emptySet=pass emptyMessage="No sized card yet — exposure fills when a run stakes something.">
  <Column id=lg title="League" />
  <Column id=bets title="Bets" />
  <Column id=games title="Games" />
  <Column id=stake_sized title="Sized" fmt='#,##0.00"u"' />
  <Column id=cap_units title="Slate cap" fmt='#,##0.0"u"' />
  <Column id=exposure title="Of bankroll" fmt='pct1' />
  <Column id=stake_solo title="Solo Kelly" fmt='#,##0.00"u"' />
</DataTable>

```sql leagues
select '%' as league, 'All' as lg, 0 as ord
union all
select distinct league, upper(league), 1
from velocity.board
where league != '__none__'
order by ord, league
```

<ButtonGroup data={leagues} name=league value=league label=lg defaultValue="%" />

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
  case when venue = 'sportsbook' then book else venue end as venue_label,
  p_model,
  edge,
  coalesce(tier, '') as tier,
  conviction,
  case when stake_sized > 0 then stake_sized end as stake_sized,
  case
    when note is not null then 'paper — ' || note
    when stake_sized > 0 then 'staked'
    else 'watch' end as status,
  kickoff
from velocity.board
where league != '__none__'
  and league like coalesce(nullif('${inputs.league}', ''), '%')
order by
  case coalesce(tier, '') when 'A' then 0 when 'B' then 1 when 'C' then 2
    when '' then 3 else 4 end,
  conviction desc nulls last,
  edge desc
```

<DataTable data={board_rows} link=matchup_link rows=40 emptySet=pass emptyMessage="No slate loaded — the board fills when the daily run publishes.">
  <Column id=lg title="League" />
  <Column id=matchup title="Matchup" />
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=point title="Line" fmt='#,##0.0' />
  <Column id=price title="Price" fmt='+#,##0;-#,##0' />
  <Column id=venue_label title="Venue" />
  <Column id=p_model title="Model %" fmt='pct1' />
  <Column id=edge title="Edge" fmt='pct1' contentType=delta />
  <Column id=tier title="Tier" />
  <Column id=stake_sized title="Stake" fmt='#,##0.00"u"' />
  <Column id=status title="Status" wrap=true />
</DataTable>

_Model output, graded in public. Edge = model probability minus the de-vigged
market probability. Stake is the portfolio-sized number after the per-game,
per-class and slate caps; a paper row says why it is not staked. The
[Performance](/performance) page carries the record; the odds archive behind
the line moves was last seen {tiles[0]?.odds_as_of ?? 'never'} UTC._

<!-- Crawl seed: the matchup template route must have at least one
     discoverable instance for the static build, even on an empty board
     (offseason). The sentinel page renders its empty states. -->
<a href="/matchup/__none__" style="display:none" aria-hidden="true">.</a>
