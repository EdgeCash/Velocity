---
title: Today
hide_title: true
sidebar_position: 1
---

```sql stamp
select max(stamp) as stamp from velocity.board where league != '__none__'
```

<PageHead
  title="Today"
  subtitle="The card the model recommends, the money already on the table, and what the record says about both."
  stamp={stamp[0]?.stamp}
/>

<LiveTicker />

```sql bank
select
  current, seed, peak, drawdown, open_exposure, open_bets, halted,
  round(drawdown * 100) as drawdownpct,
  round(halt_threshold * 100) as haltpct,
  case when seed > 0 then current / seed - 1 end as growth
from velocity.bankroll
where league != '__none__'
```

```sql today
select
  (select coalesce(sum(stake_sized), 0) from velocity.exposure
    where league != '__none__') as sized,
  (select coalesce(sum(cap_units), 0) from velocity.exposure
    where league != '__none__') as cap,
  (select count(*) from velocity.publish
    where league != '__none__' and published) as plays,
  (select count(*) from velocity.board
    where league != '__none__') as priced,
  (select count(distinct game_id) from velocity.games
    where league != '__none__') as games
```

```sql season
select
  coalesce(sum(coalesce(profit_sized, profit)), 0) as units,
  count(*) filter (result = 'win') as wins,
  count(*) filter (result = 'loss') as losses,
  count(distinct slate_date) as days
from velocity.cumulative_record
where league != '__none__' and result in ('win','loss','push')
  and coalesce(stake, 0) > 0
```

<StatRow>
  <StatCard
    label="Bankroll"
    value={bank[0]?.current}
    format="units"
    accent="brand"
    delta={bank[0]?.growth}
    sub={bank[0] ? `from ${bank[0].seed}u seed` : 'no ledger yet'}
  />
  <StatCard
    label="Today's exposure"
    value={today[0]?.sized}
    format="units"
    meter={today[0]?.cap > 0 ? today[0].sized / today[0].cap : null}
    sub={today[0]?.cap > 0 ? `of ${today[0].cap.toFixed(1)}u slate cap` : 'nothing staked'}
  />
  <StatCard
    label="On the card"
    value={today[0]?.plays}
    format="number"
    dp={0}
    sub={`${today[0]?.priced ?? 0} priced · ${today[0]?.games ?? 0} games`}
  />
  <StatCard
    label="Season"
    value={season[0]?.units}
    format="units"
    sign={true}
    accent="money"
    sub={season[0]?.days > 0
      ? `${season[0].wins}-${season[0].losses} over ${season[0].days} graded day(s)`
      : 'no graded days yet'}
  />
</StatRow>

{#if bank[0]?.halted}
<Alert status="negative">

**Kill-switch tripped.** The bankroll is {bank[0]?.drawdownpct}% below its peak,
past the {bank[0]?.haltpct}% halt. Every stake on today's card is zero until the
ledger is adjusted.

</Alert>
{/if}

```sql leagues
select '%' as league, 'All' as lg, 0 as ord
union all
select distinct league, upper(league), 1
from velocity.publish
where league != '__none__' and published
order by ord, league
```

```sql card
select
  p.game_id, p.league, p.market, p.side, p.price, p.player,
  p.tier, p.conviction, p.edge, p.home_team, p.away_team, p.kickoff,
  coalesce(p.stake_sized, p.stake) as stake,
  b.point, b.book, b.venue, b.rationale
from velocity.publish p
left join velocity.board b
  on b.league = p.league and b.game_id = p.game_id
  and b.market = p.market and b.side = p.side
where p.league != '__none__' and p.published
  and p.league like coalesce(nullif('${inputs.league}', ''), '%')
order by p.conviction desc, p.edge desc
```

```sql card_total
select count(*) as n, coalesce(sum(coalesce(stake_sized, stake)), 0) as units
from velocity.publish
where league != '__none__' and published
  and league like coalesce(nullif('${inputs.league}', ''), '%')
```

<SectionBar
  title="The card"
  meta={card_total[0]?.n > 0
    ? `${card_total[0].n} plays · ${card_total[0].units.toFixed(2)}u`
    : ''}
  tone="brand"
/>

<ButtonGroup data={leagues} name=league value=league label=lg defaultValue="%" />

{#if card.length > 0}
  <div class="play-list">
    {#each card as play}
      <PlayCard {...play} />
    {/each}
  </div>
{:else}
  <EmptyNote
    title="No play cleared the gate"
    detail="A quiet day is the gate doing its job: the model priced the board and nothing met the conviction floor, the corroboration rule and the edge band at once. The full board is still below."
  />
{/if}

<SectionBar title="Rest of the board" meta={`${(today[0]?.priced ?? 0) - (today[0]?.plays ?? 0)} priced, not on the card`} />

Every other market the model priced today, with the rule that held it back.
Paper rows are priced and graded for closing-line value but never staked.

```sql held
select
  upper(p.league) as lg,
  p.away_team || ' @ ' || p.home_team as matchup,
  '/matchup/' || p.game_id as matchup_link,
  case p.market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'ML'
    when 'team_total_home' then 'TT home' when 'team_total_away' then 'TT away'
    else p.market end as market,
  upper(p.side) as side,
  b.point,
  p.price,
  p.edge,
  p.conviction,
  p.reason
from velocity.publish p
left join velocity.board b
  on b.league = p.league and b.game_id = p.game_id
  and b.market = p.market and b.side = p.side
where p.league != '__none__' and not p.published
  and p.league like coalesce(nullif('${inputs.league}', ''), '%')
order by p.conviction desc nulls last
```

<DataTable data={held} link=matchup_link rows=8 compact={true} rowShading={false}
  emptySet=pass emptyMessage="Everything the model priced today made the card.">
  <Column id=lg title="Lg" />
  <Column id=matchup title="Matchup" />
  <Column id=market title="Market" />
  <Column id=side title="Side" />
  <Column id=point title="Line" fmt='#,##0.0' align=right />
  <Column id=price title="Price" fmt='+#,##0;-#,##0' align=right />
  <Column id=edge title="Edge" fmt='0.0%' align=right />
  <Column id=conviction title="Conv" fmt='0.00' align=right />
  <Column id=reason title="Why it sat" wrap={true} />
</DataTable>

_The full board, every venue and price, is on the [Board](/board) page. Edge is
the model probability minus the de-vigged market probability; stake is the
portfolio-sized number after the per-game, per-class and slate caps._

<style>
  .play-list {
    display: grid;
    gap: 0.5rem;
    margin: 0.2rem 0 0.6rem;
  }
  @media (min-width: 1000px) {
    .play-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
</style>
