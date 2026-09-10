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


```sql sizing
select
  upper(p.league) as lg,
  coalesce(b.away_team || ' @ ' || b.home_team, p.game_id) as matchup,
  case p.market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'ML'
    when 'team_total_home' then 'TT home' when 'team_total_away' then 'TT away'
    else replace(p.market, '_', ' ') end as market,
  upper(p.side) as side,
  p.stake_solo as solo,
  p.stake as sized,
  case when p.stake_solo > 0 then p.stake / p.stake_solo end as kept,
  count(*) over (partition by p.game_id) as gamebets
from velocity.portfolio p
left join (
  select distinct game_id, home_team, away_team from velocity.board
) b on b.game_id = p.game_id
where p.league != '__none__' and coalesce(p.stake_solo, 0) > 0
order by p.stake_solo desc
```

```sql trimmed
select
  count(*) as n,
  count(*) filter (stake < stake_solo - 0.0001) as cut,
  coalesce(sum(stake_solo), 0) as solo,
  coalesce(sum(stake), 0) as sized
from velocity.portfolio
where league != '__none__' and coalesce(stake_solo, 0) > 0
```

<SectionBar
  title="Sizing"
  meta={trimmed[0]?.n > 0
    ? `${trimmed[0].cut} of ${trimmed[0].n} cut below their own Kelly`
    : ''}
/>

Kelly sizes each bet as if it were the only one on the board. Two bets on the
same game are not two independent bets, so a game's bets are de-scaled
together by **1 / (1 + (n − 1)ρ)** before any cap applies — three bets on one
game each keep half their standalone stake at ρ = 0.5. **Kept** is what
survived that and the per-game, per-class and slate caps.

<DataTable data={sizing} rows=12 compact={true} rowShading={false} emptySet=pass
  emptyMessage="Sizing fills when a run stakes something.">
  <Column id=lg title="Lg" />
  <Column id=matchup title="Matchup" />
  <Column id=market title="Market" />
  <Column id=side title="Side" />
  <Column id=gamebets title="Bets on game" align=right />
  <Column id=solo title="Solo Kelly" fmt='#,##0.00"u"' align=right />
  <Column id=sized title="Sized" fmt='#,##0.00"u"' align=right />
  <Column id=kept title="Kept" fmt='0%' align=right />
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

<div class:lone-filter={leagues.length <= 1}>

<ButtonGroup data={leagues} name=league value=league label=lg defaultValue="%" />

</div>

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
  venue_label,
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
  <Column id=price title="Price" fmt='+0;−0' align=right />
  <Column id=venue_label title="Venue" />
  <Column id=edge title="Edge" fmt='+0.0%;−0.0%' align=right contentType=delta deltaSymbol={false} />
  <Column id=tier title="Tier" align=center />
  <Column id=stake_sized title="Stake" fmt='#,##0.00"u"' align=right />
  <Column id=status title="Status" align=center chip={true} />
</DataTable>

```sql moved
select
  upper(m.league) as lg,
  coalesce(b.away_team || ' @ ' || b.home_team, m.game_id) as matchup,
  case m.market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'ML'
    when 'team_total_home' then 'TT home' when 'team_total_away' then 'TT away'
    else replace(m.market, '_', ' ') end as market,
  upper(m.side) as side,
  m.point_open, m.point_now,
  case when m.market != 'moneyline' then m.point_now - m.point_open end as ptmove,
  m.price_open, m.price_now,
  m.price_now - m.price_open as prmove,
  case when b.stake_sized > 0 then 'staked' end as onthecard
from velocity.line_moves m
left join velocity.board b
  on b.game_id = m.game_id and b.market = m.market and b.side = m.side
where m.league != '__none__'
  and m.league like coalesce(nullif('${inputs.league}', ''), '%')
  and (coalesce(m.point_now, 0) != coalesce(m.point_open, 0)
       or coalesce(m.price_now, 0) != coalesce(m.price_open, 0))
order by abs(coalesce(m.point_now, 0) - coalesce(m.point_open, 0)) desc,
         abs(coalesce(m.price_now, 0) - coalesce(m.price_open, 0)) desc
```

<SectionBar title="Moved since open" meta={`${moved.length ?? 0} markets on the move`} />

What the hourly odds archive has seen change since it first priced the game.
A matchup page carries the same movement one game at a time; this is every
game at once, so a number running away is visible without opening sixteen of
them. Whether a move helped or hurt is the closing-line
calculation on [Performance](/performance), which is measured against the
close rather than guessed from the direction.

<DataTable data={moved} rows=10 compact={true} rowShading={false} emptySet=pass
  emptyMessage="Nothing has moved yet. Movement appears once the hourly archive has seen a game more than once.">
  <Column id=lg title="Lg" />
  <Column id=matchup title="Matchup" />
  <Column id=market title="Market" />
  <Column id=side title="Side" />
  <Column id=point_open title="Open" fmt='#,##0.0' align=right />
  <Column id=point_now title="Now" fmt='#,##0.0' align=right />
  <Column id=ptmove title="Line move" fmt='+#,##0.0;−#,##0.0' align=right contentType=delta deltaSymbol={false} />
  <Column id=price_open title="Open px" fmt='+0;−0' align=right />
  <Column id=price_now title="Now px" fmt='+0;−0' align=right />
  <Column id=prmove title="Px move" fmt='+0;−0' align=right contentType=delta deltaSymbol={false} />
  <Column id=onthecard title="" align=center />
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
  <Column id=price title="Price" fmt='+0;−0' align=right />
  <Column id=p_win title="Win" fmt='0.0%' align=right />
  <Column id=ev title="EV" fmt='+0.0%;−0.0%' align=right contentType=delta deltaSymbol={false} />
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

<style>
  /* A filter with one option is noise, but it still has to mount: the
     input it declares is what the page's queries are templated on. */
  .lone-filter { display: none; }
</style>
