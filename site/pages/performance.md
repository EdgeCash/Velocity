---
title: Performance
sidebar_position: 3
hide_title: true
---

```sql stamp
select max(stamp) as stamp from velocity.record where league != '__none__'
```

<PageHead
  title="Performance"
  subtitle="What the model actually earned, and whether it beat the number it closed at. Losses are shown as plainly as wins — the record is the product."
  stamp={stamp[0]?.stamp}
/>

```sql bank
select
  current, seed, peak, drawdown, open_exposure, open_bets, settled_bets,
  staked, profit, halted, halt_threshold as haltthreshold,
  case when staked > 0 then profit / staked end as roi,
  case when seed > 0 then current / seed - 1 end as growth,
  case mode
    when 'auto' then 'Booked automatically at the recommended terms.'
    when 'manual' then 'Booked as the operator recorded them.'
    else '' end as modenote
from velocity.bankroll
where league != '__none__'
```

```sql season
select
  count(*) filter (result = 'win') as wins,
  count(*) filter (result = 'loss') as losses,
  count(*) filter (result = 'push') as pushes,
  count(*) filter (result in ('win','loss')) as decided,
  coalesce(sum(coalesce(profit_sized, profit)), 0) as units,
  coalesce(sum(stake), 0) as staked,
  strftime(min(slate_date), '%b %-d') as since,
  count(distinct slate_date) as days
from velocity.cumulative_record
where league != '__none__' and result in ('win','loss','push')
  and coalesce(stake, 0) > 0
```

```sql season_rate
select *, case when decided > 0 then wins::double / decided end as win_rate
from ${season}
```

```sql clv_head
select
  avg(r.line_clv) filter (r.line_clv is not null) as line_clv,
  count(*) filter (r.line_clv is not null or r.price_clv is not null) as with_close,
  count(*) filter (r.line_clv is not null or r.price_clv is not null)
    || ' bets · ' || printf('%+.2f', avg(r.line_clv)) || ' pts' as clv_label,
  avg(case
        when r.line_clv is not null then case when r.line_clv > 0 then 1.0 when r.line_clv < 0 then 0.0 end
        when r.price_clv is not null then case when r.price_clv > 0 then 1.0 when r.price_clv < 0 then 0.0 end
      end) as beat_close
from velocity.cumulative_record r
where r.league != '__none__' and r.result in ('win','loss','push')
  and exists (select 1 from velocity.clv_by_market c
              where c.league = r.league and c.market = r.market and c.clv_trusted)
```

<StatRow>
  <StatCard
    label="Bankroll"
    value={bank[0]?.seed > 0 ? bank[0].current : null}
    format="units"
    accent="brand"
    delta={bank[0]?.seed > 0 ? bank[0].growth : null}
    sub={bank[0]?.seed > 0 ? `peak ${bank[0].peak.toFixed(2)}u` : 'no ledger yet'}
  />
  <StatCard
    label="Season units"
    value={season_rate[0]?.units}
    format="units"
    sign={true}
    accent="money"
    sub={season_rate[0]?.days > 0
      ? `${season_rate[0].wins}-${season_rate[0].losses}-${season_rate[0].pushes} since ${season_rate[0].since}`
      : 'nothing graded yet'}
  />
  <StatCard
    label="Return on stake"
    value={season_rate[0]?.staked > 0 ? season_rate[0].units / season_rate[0].staked : null}
    format="percent"
    sign={true}
    accent="money"
    sub={season_rate[0]?.staked > 0 ? `on ${season_rate[0].staked.toFixed(1)}u staked` : ''}
  />
  <StatCard
    label="Win rate"
    value={season_rate[0]?.win_rate}
    format="percent"
    sub={season_rate[0]?.decided > 0 ? `${season_rate[0].decided} decided` : 'no decided bets'}
  />
  <StatCard
    label="Beat the close"
    value={clv_head[0]?.with_close > 0 ? clv_head[0].beat_close : null}
    format="percent"
    sub={clv_head[0]?.with_close > 0 ? clv_head[0].clv_label : 'no close matched yet'}
    hint="Trusted markets only: spreads, totals and moneylines."
  />
</StatRow>

{#if bank[0]?.halted}
<Alert status="negative">

**Kill-switch tripped.** The bankroll sits more than
{Math.round((bank[0]?.haltthreshold ?? 0.3) * 100)}% below its peak. The next
card stakes nothing until the ledger is adjusted.

</Alert>
{/if}

```sql curve
select recorded_at, bankroll, record_type, amount, market, result
from velocity.bankroll_curve
where league != '__none__'
order by recorded_at
```

<SectionBar
  title="Bankroll"
  meta={bank[0]?.seed > 0 ? `${bank[0].settled_bets} settled · ${bank[0].open_bets} open` : ''}
/>

{#if curve.length > 1}

<LineChart
  data={curve}
  x=recorded_at
  y=bankroll
  yAxisTitle="units"
  lineColor="#2bb3ab"
  lineWidth={2}
  markers={false}
  yMin={0}
  chartAreaHeight={190}
/>

_One bankroll, seeded once, moved only by settled bets and recorded
adjustments. Open bets do not move it until they settle._

{#if bank[0]?.modenote}

_{bank[0].modenote}_

{/if}

{:else}
<EmptyNote
  title="The curve starts at the first settled bet"
  detail="The ledger holds the seed, every recommendation and every placed bet; the line draws once the morning grade settles the first of them."
/>
{/if}

```sql clv_market
select
  upper(league) as lg,
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'Moneyline'
    when 'team_total_home' then 'Team total (H)'
    when 'team_total_away' then 'Team total (A)'
    when 'parlay' then 'Parlay'
    else replace(market, '_', ' ') end as market_label,
  n_bets,
  units,
  case when clv_trusted then mean_line_clv end as line_clv,
  case when clv_trusted then pct_beat_close end as beat_close,
  case when clv_trusted then 'CLV' else 'P/L' end as judged_on
from velocity.clv_by_market
where league != '__none__'
order by league, clv_trusted desc, n_bets desc
```

<SectionBar title="By market" meta="closing-line value where the close is sharp" />

Spreads, totals and moneylines close efficiently enough that beating the number
is skill. Props and team totals do not, so their rows carry no CLV and are
judged on profit and loss instead.

<DataTable data={clv_market} groupBy=lg compact={true} rowShading={false}
  emptySet=pass emptyMessage="Per-market results fill as graded plays accumulate.">
  <Column id=market_label title="Market" />
  <Column id=n_bets title="Bets" align=right />
  <Column id=units title="Units" fmt='+#,##0.00;−#,##0.00' align=right contentType=delta />
  <Column id=line_clv title="Line CLV" fmt='+#,##0.00;−#,##0.00' align=right contentType=delta />
  <Column id=beat_close title="Beat close" fmt='0%' align=right />
  <Column id=judged_on title="Judged on" align=center chip={true} />
</DataTable>

```sql units_by_day
select slate_date, sum(units_sized) as units
from velocity.units
where league != '__none__'
group by slate_date
order by slate_date
```

<SectionBar title="Units over time" meta="every league, at the sized stake" />

{#if units_by_day.length > 1}

<LineChart
  data={units_by_day}
  x=slate_date
  y=units
  yAxisTitle="cumulative units"
  lineColor="#2bb3ab"
  lineWidth={2}
  chartAreaHeight={170}
/>

{:else}
<EmptyNote
  title="One graded day so far"
  detail="The line needs a second settled day before it says anything. Until then the numbers above are the honest read."
/>
{/if}

```sql recent
select
  upper(league) as lg,
  strftime(slate_date, '%b %-d') as day,
  case when length(play) > 46 then substr(play, 1, 44) || '…' else play end as play,
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'ML'
    when 'team_total_home' then 'TT home' when 'team_total_away' then 'TT away'
    when 'parlay' then 'Parlay'
    else replace(market, '_', ' ') end as market,
  upper(side) as side,
  point,
  price,
  result,
  coalesce(profit_sized, profit) as profit,
  line_clv
from velocity.cumulative_record
where league != '__none__' and result in ('win','loss','push')
  and coalesce(stake, 0) > 0
order by slate_date desc, abs(coalesce(profit_sized, profit)) desc
```

<SectionBar title="Settled" meta={`${recent.length ?? 0} graded plays`} />

<DataTable data={recent} rows=12 compact={true} rowShading={false} search={true}
  emptySet=pass emptyMessage="Yesterday's grading appears after the morning run.">
  <Column id=day title="Day" />
  <Column id=lg title="Lg" />
  <Column id=play title="Play" />
  <Column id=market title="Market" />
  <Column id=side title="Side" />
  <Column id=point title="Line" fmt='#,##0.0' align=right />
  <Column id=price title="Price" fmt='+#,##0;-#,##0' align=right />
  <Column id=result title="Result" align=center chip={true} />
  <Column id=profit title="Profit" fmt='+#,##0.00;−#,##0.00' align=right contentType=delta />
  <Column id=line_clv title="CLV" fmt='+#,##0.0;−#,##0.0' align=right contentType=delta />
</DataTable>

```sql pending
select
  (select count(*) from velocity.record
    where league != '__none__' and result = 'pending') as pending,
  (select count(*) from velocity.cumulative_record
    where league != '__none__' and result in ('win','loss','push')
      and coalesce(stake, 0) = 0) as paper_settled,
  (select count(*) filter (result = 'win') from velocity.cumulative_record
    where league != '__none__' and coalesce(stake, 0) = 0) as paperwins,
  (select count(*) filter (result = 'loss') from velocity.cumulative_record
    where league != '__none__' and coalesce(stake, 0) = 0) as paperlosses
```

_{pending[0]?.pending ?? 0} play(s) are still pending a final. Paper calls —
priced and graded, never staked — sit outside the headline at
{pending[0]?.paperwins ?? 0}-{pending[0]?.paperlosses ?? 0}. Per-market
flags and the trailing windows are on [Market health](/health)._
