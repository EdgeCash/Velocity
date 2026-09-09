---
title: Performance
---

```sql season
select
  count(*) filter (result = 'win') as wins,
  count(*) filter (result = 'loss') as losses,
  count(*) filter (result = 'push') as pushes,
  coalesce(sum(profit), 0) as units,
  coalesce(sum(coalesce(profit_sized, profit)), 0) as units_sized,
  count(*) filter (profit_sized is null) as unsized_rows,
  count(*) filter (result in ('win','loss')) as decided,
  strftime(min(slate_date), '%Y-%m-%d') as since,
  count(distinct slate_date) as days
from velocity.cumulative_record
where league != '__none__' and result in ('win','loss','push')
  and coalesce(stake, 0) > 0
```

```sql season_rate
select wins, losses, pushes, units, units_sized, unsized_rows, since, days,
  case when decided > 0 then wins / decided end as win_rate
from ${season}
```

```sql pending
select
  (select count(*) from velocity.record
    where league != '__none__' and result = 'pending') as pending,
  (select count(*) from velocity.cumulative_record
    where league != '__none__' and result in ('win','loss','push')
      and coalesce(stake, 0) = 0) as paper_settled,
  (select count(*) filter (result = 'win') from velocity.cumulative_record
    where league != '__none__' and coalesce(stake, 0) = 0) as paper_wins,
  (select count(*) filter (result = 'loss') from velocity.cumulative_record
    where league != '__none__' and coalesce(stake, 0) = 0) as paper_losses
```

<BigValue data={season_rate} value=units_sized title="Season units (sized stakes)" fmt='+#,##0.0"U"' />
<BigValue data={season_rate} value=units title="Season units (solo Kelly)" fmt='+#,##0.0"U"' />
<BigValue data={season_rate} value=win_rate title="Win rate (decided)" fmt='pct1' />
<BigValue data={season_rate} value=wins title="Wins" />
<BigValue data={season_rate} value=losses title="Losses" />
<BigValue data={pending} value=pending title="Pending" />

_Record since {season_rate[0]?.since ?? '—'}
over {season_rate[0]?.days ?? 0} graded day(s). Sized units are at the
portfolio stake the card recommended; solo units at the uncapped Kelly stake
the slate keeps for backtest comparability. {season_rate[0]?.unsized_rows ?? 0}
row(s) predate sized stakes and count at their solo stake in the sized line.
Paper calls — priced, graded, never staked — sit outside the headline:
{pending[0]?.paper_wins ?? 0}-{pending[0]?.paper_losses ?? 0} so far._

## Closing line value

The professional's yardstick: did each bet beat the number it closed at?
Units are noisy; CLV converges fast — but only on markets whose close is
sharp. Spreads, totals and moneylines are; props, team totals and thin
derivatives are not, and their rows say so instead of carrying a number.

```sql clv_market
select
  upper(league) as lg,
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'Moneyline'
    when 'team_total_home' then 'Team total (H)'
    when 'team_total_away' then 'Team total (A)'
    when 'parlay' then 'Parlay'
    else market end as market_label,
  n_bets,
  units,
  case when clv_trusted then mean_line_clv end as line_clv,
  case when clv_trusted then mean_price_clv end as price_clv,
  case when clv_trusted then pct_beat_close end as beat_close,
  case when clv_trusted then 'CLV is the yardstick' else 'judge on P/L' end as verdict
from velocity.clv_by_market
where league != '__none__'
order by league, clv_trusted desc, n_bets desc
```

<DataTable data={clv_market} groupBy=lg emptySet=pass emptyMessage="Per-market CLV fills as graded plays match an archived close.">
  <Column id=market_label title="Market" />
  <Column id=n_bets title="Bets" />
  <Column id=units title="Units" fmt='+#,##0.0;-#,##0.0' contentType=delta />
  <Column id=line_clv title="Line CLV (pts)" fmt='+#,##0.00;-#,##0.00' contentType=delta />
  <Column id=price_clv title="Price CLV" fmt='+#,##0.000;-#,##0.000' contentType=delta />
  <Column id=beat_close title="Beat the close" fmt='pct0' />
  <Column id=verdict title="Read it as" />
</DataTable>

```sql clv
select
  count(*) as graded,
  count(*) filter (r.line_clv is not null or r.price_clv is not null) as with_close,
  avg(r.line_clv) filter (r.line_clv is not null) as mean_line_clv,
  avg(r.price_clv) filter (r.price_clv is not null) as mean_price_clv,
  avg(case
        when r.line_clv is not null then case when r.line_clv > 0 then 1.0 when r.line_clv < 0 then 0.0 end
        when r.price_clv is not null then case when r.price_clv > 0 then 1.0 when r.price_clv < 0 then 0.0 end
      end) as pct_beat_close
from velocity.cumulative_record r
where r.league != '__none__' and r.result in ('win','loss','push')
  and exists (select 1 from velocity.clv_by_market c
              where c.league = r.league and c.market = r.market and c.clv_trusted)
```

<BigValue data={clv} value=mean_line_clv title="Mean line CLV (pts), trusted markets" fmt='+#,##0.00;-#,##0.00' />
<BigValue data={clv} value=pct_beat_close title="Beat the close" fmt='pct1' />
<BigValue data={clv} value=with_close title="Bets with a close" />

```sql clv_by_day
select r.slate_date, upper(r.league) as lg,
  avg(r.line_clv) as clv
from velocity.cumulative_record r
where r.league != '__none__' and r.line_clv is not null
  and exists (select 1 from velocity.clv_by_market c
              where c.league = r.league and c.market = r.market and c.clv_trusted)
group by r.slate_date, r.league
order by r.slate_date
```

<LineChart
  data={clv_by_day}
  x=slate_date
  y=clv
  series=lg
  yAxisTitle="mean line CLV (pts) per day, trusted markets"
  emptySet=pass
  emptyMessage="CLV accrues once graded plays match an archived close (the hourly odds snapshots)."
/>

## Units over time

```sql units_by_day
select slate_date, league, units, units_sized
from velocity.units
where league != '__none__'
order by slate_date
```

<LineChart
  data={units_by_day}
  x=slate_date
  y=units_sized
  series=league
  yAxisTitle="cumulative units at sized stakes"
  emptySet=pass
  emptyMessage="The record chart draws as graded days accumulate."
/>

## By league

```sql by_league
select
  upper(league) as lg,
  count(*) filter (result = 'win') as w,
  count(*) filter (result = 'loss') as l,
  count(*) filter (result = 'push') as p,
  coalesce(sum(coalesce(profit_sized, profit)), 0) as units_sized,
  coalesce(sum(profit), 0) as units
from velocity.cumulative_record
where league != '__none__' and result in ('win','loss','push')
  and coalesce(stake, 0) > 0
group by league
order by units_sized desc
```

<DataTable data={by_league} emptySet=pass emptyMessage="No graded plays yet.">
  <Column id=lg title="League" />
  <Column id=w title="W" />
  <Column id=l title="L" />
  <Column id=p title="P" />
  <Column id=units_sized title="Units (sized)" fmt='+#,##0.0' contentType=delta />
  <Column id=units title="Units (solo)" fmt='+#,##0.0' contentType=delta />
</DataTable>

## Latest graded slate

```sql latest_graded
select upper(league) as lg, section, play, upper(side) as side, point, price,
  result, profit, stake_sized, profit_sized
from velocity.record
where league != '__none__' and result is not null
order by league, case result when 'pending' then 1 else 0 end, section
```

<DataTable data={latest_graded} rows=30 emptySet=pass emptyMessage="Yesterday's grading appears after the morning run.">
  <Column id=lg title="League" />
  <Column id=section title="Section" />
  <Column id=play title="Play" />
  <Column id=result title="Result" />
  <Column id=stake_sized title="Stake" fmt='#,##0.00"u"' />
  <Column id=profit_sized title="Profit (sized)" fmt='+#,##0.00' contentType=delta />
  <Column id=profit title="Profit (solo)" fmt='+#,##0.00' contentType=delta />
</DataTable>

_Losses shown as plainly as wins — the record is the product._
