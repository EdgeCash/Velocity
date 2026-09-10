---
title: Board
sidebar_position: 2
hide_title: true
---

```sql stamp
select max(stamp) as stamp from velocity.board where league != '__none__'
```

<PageHead
  title="Board"
  subtitle="Every market the model priced today, at the best price it found, ranked by tier then conviction. This is the whole opinion; the card on Today is the part that cleared the gate."
  stamp={stamp[0]?.stamp}
/>

```sql exposure_rows
select upper(league) as lg, bets, games, stake_sized, cap_units,
  exposure, stake_solo
from velocity.exposure
where league != '__none__'
order by stake_sized desc
```

<SectionBar title="Exposure" meta="sized against each league's slate cap" />

<DataTable data={exposure_rows} compact={true} rowShading={false} emptySet=pass
  emptyMessage="No sized card yet — exposure fills when a run stakes something.">
  <Column id=lg title="Lg" />
  <Column id=bets title="Bets" align=right />
  <Column id=games title="Games" align=right />
  <Column id=stake_sized title="Sized" fmt='#,##0.00"u"' align=right />
  <Column id=cap_units title="Cap" fmt='#,##0.0"u"' align=right />
  <Column id=exposure title="Of bankroll" fmt='0.0%' align=right />
  <Column id=stake_solo title="Solo Kelly" fmt='#,##0.00"u"' align=right />
</DataTable>

```sql leagues
select '%' as league, 'All' as lg, 0 as ord
union all
select distinct league, upper(league), 1
from velocity.board
where league != '__none__'
order by ord, league
```

```sql board_count
select count(*) as n, count(*) filter (stake_sized > 0) as staked
from velocity.board
where league != '__none__'
  and league like coalesce(nullif('${inputs.league}', ''), '%')
```

<SectionBar
  title="Priced markets"
  meta={`${board_count[0]?.n ?? 0} priced · ${board_count[0]?.staked ?? 0} staked`}
/>

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
  edge,
  coalesce(tier, '') as tier,
  conviction,
  case when stake_sized > 0 then stake_sized end as stake_sized,
  case
    when note is not null then 'paper'
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

<DataTable data={board_rows} link=matchup_link rows=25 compact={true} rowShading={false}
  emptySet=pass emptyMessage="No slate loaded — the board fills when the daily run publishes.">
  <Column id=lg title="Lg" />
  <Column id=matchup title="Matchup" />
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=point title="Line" fmt='#,##0.0' align=right />
  <Column id=price title="Price" fmt='+#,##0;-#,##0' align=right />
  <Column id=venue_label title="Venue" />
  <Column id=edge title="Edge" fmt='0.0%' align=right contentType=delta />
  <Column id=tier title="Tier" align=center />
  <Column id=stake_sized title="Stake" fmt='#,##0.00"u"' align=right />
  <Column id=status title="Status" align=center chip={true} />
</DataTable>

```sql parlays
select
  upper(league) as lg,
  legs, n_legs, price, p_win, ev, stake,
  case when same_game then 'same game' else '' end as caveat
from velocity.parlays
where league != '__none__'
  and league like coalesce(nullif('${inputs.league}', ''), '%')
order by ev desc
```

<SectionBar title="Parlays" meta="joint probability simulated on the same game outcomes" />

A combination clears only if its simulated joint probability beats the product
price. A **same game** ticket is flagged because books reprice correlated legs:
its EV is the most it could be, not what the book will pay.

<DataTable data={parlays} rows=6 compact={true} rowShading={false} emptySet=pass
  emptyMessage="No parlay cleared the combined-EV bar today.">
  <Column id=lg title="Lg" />
  <Column id=legs title="Legs" wrap={true} />
  <Column id=n_legs title="#" align=right />
  <Column id=price title="Price" fmt='+#,##0;-#,##0' align=right />
  <Column id=p_win title="Win" fmt='0.0%' align=right />
  <Column id=ev title="EV" fmt='0.0%' align=right contentType=delta />
  <Column id=stake title="Stake" fmt='#,##0.00"u"' align=right />
  <Column id=caveat title="" align=center chip={true} />
</DataTable>

_Edge is the model probability minus the de-vigged market probability. Stake is
the portfolio-sized number after the per-game, per-class and slate caps. A
**paper** row is priced and graded for closing-line value but never wagered — a
market without a promoted edge, or an edge past the adverse-selection ceiling.
The [Performance](/performance) page carries what all of it earned._

<!-- Crawl seed: the matchup template route needs one discoverable instance
     for the static build, even on an empty board (offseason). The sentinel
     page renders its own empty states. -->
<a href="/matchup/__none__" style="display:none" aria-hidden="true">.</a>
