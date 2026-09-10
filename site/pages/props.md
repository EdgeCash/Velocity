---
title: Props
sidebar_position: 3
hide_title: true
---

```sql stamp
select max(stamp) as stamp from velocity.props where league != '__none__'
```

<PageHead
  title="Player props"
  subtitle="Every player market the model priced today, from the correlated per-game simulation. Props close loosely, so they are judged on profit and loss rather than closing-line value."
  stamp={stamp[0]?.stamp}
/>

```sql head
select
  count(*) as priced,
  count(*) filter (coalesce(stake, 0) > 0) as staked,
  coalesce(sum(stake), 0) as units,
  count(distinct player) as players,
  max(edge) as bestedge
from velocity.props
where league != '__none__'
```

<StatRow min="9rem">
  <StatCard label="Priced" value={head[0]?.priced} dp={0}
    sub={head[0]?.players > 0 ? `${head[0].players} players` : 'nothing priced'} />
  <StatCard label="Staked" value={head[0]?.staked} dp={0} accent="brand"
    sub={head[0]?.staked > 0 ? `${head[0].units.toFixed(2)}u` : 'none cleared the gate'} />
  <StatCard label="Best edge" value={head[0]?.bestedge} format="percent" sign={true}
    accent="money" sub="belief minus market" />
</StatRow>

```sql leagues
select '%' as league, 'All' as lg, 0 as ord
union all
select distinct league, upper(league), 1
from velocity.props
where league != '__none__'
order by ord, league
```

```sql markets
select '%' as market, 'All' as ml, 0 as ord
union all
select distinct market, market_label, 1
from velocity.props
where league != '__none__'
order by ord, market
```

<SectionBar title="The prop board" meta="ranked by edge" />

<div class:lone-filter={leagues.length <= 1}>

<ButtonGroup data={leagues} name=league value=league label=lg defaultValue="%" />

</div>

<div class:lone-filter={markets.length <= 1}>

<ButtonGroup data={markets} name=market value=market label=ml defaultValue="%" />

</div>

```sql rows
select
  upper(league) as lg,
  player,
  market_label,
  upper(side) as side,
  point,
  price,
  venue_label,
  p_fair,
  p_model,
  edge,
  case when coalesce(stake, 0) > 0 then stake end as stake,
  case
    when note is not null and note != '' then note
    when coalesce(stake, 0) > 0 then 'staked'
    else 'watch' end as status
from velocity.props
where league != '__none__'
  and league like coalesce(nullif('${inputs.league}', ''), '%')
  and market like coalesce(nullif('${inputs.market}', ''), '%')
order by coalesce(stake, 0) desc, edge desc
```

{#if rows.length > 0}

<DataTable data={rows} rows=25 compact={true} rowShading={false} search={true}>
  <Column id=lg title="Lg" />
  <Column id=player title="Player" />
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=point title="Line" fmt='#,##0.0' align=right />
  <Column id=price title="Price" fmt='+0;−0' align=right />
  <Column id=venue_label title="Venue" />
  <Column id=p_fair title="Market" fmt='pct1' align=right />
  <Column id=p_model title="Belief" fmt='pct1' align=right />
  <Column id=edge title="Edge" fmt='+0.0%;−0.0%' align=right contentType=delta deltaSymbol={false} />
  <Column id=stake title="Stake" fmt='#,##0.00"u"' align=right />
  <Column id=status title="Status" wrap={true} />
</DataTable>

{:else}
<EmptyNote
  title="No player prop priced today"
  detail="The prop board needs a projection source for the league. The football props ride on the FantasyPros pull, so a slate without one prices the game markets only — the model had no opinion to withhold."
/>
{/if}

<SectionBar title="How to read it" />

- **Market** is the de-vigged price at the shopped book; **Belief** is the
  model's number after the same market anchoring the game board uses. The
  gap between them is the edge, and the prop gate wants more of it than a
  game market does — a prop line is thinner and the price is worse.
- **A prop carries no closing-line value.** Few sharps price a receptions
  line, so its close is not a yardstick; the trailing windows on
  [Market health](/health) judge props on realised profit instead, which is
  why a prop market needs a bigger sample before it says anything.
- **Status** is why a row sat: a market without a promoted edge, an edge past
  the adverse-selection ceiling, or a per-class cap already spent.

<style>
  /* Mounted always, hidden when there is nothing to choose: the ButtonGroup
     is what declares the input the queries below are templated on. */
  .lone-filter { display: none; }
</style>
