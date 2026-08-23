---
title: Performance
---

```sql season
select
  count(*) filter (result = 'win') as wins,
  count(*) filter (result = 'loss') as losses,
  count(*) filter (result = 'push') as pushes,
  coalesce(sum(profit), 0) as units,
  count(*) filter (result in ('win','loss')) as decided
from velocity.cumulative_record
where league != '__none__' and result in ('win','loss','push')
```

```sql season_rate
select wins, losses, pushes, units,
  case when decided > 0 then wins / decided end as win_rate
from ${season}
```

<BigValue data={season_rate} value=units title="Season units" fmt='+#,##0.0"U"' />
<BigValue data={season_rate} value=win_rate title="Win rate (decided)" fmt='pct1' />
<BigValue data={season_rate} value=wins title="Wins" />
<BigValue data={season_rate} value=losses title="Losses" />

## Closing line value

The professional's yardstick: did each bet beat the number it closed at?
Units are noisy; CLV converges fast. Line CLV is signed points beaten
(spreads/totals); price CLV is the moneyline's decimal-odds edge vs close.

```sql clv
select
  count(*) as graded,
  count(*) filter (line_clv is not null or price_clv is not null) as with_close,
  avg(line_clv) filter (line_clv is not null) as mean_line_clv,
  avg(price_clv) filter (price_clv is not null) as mean_price_clv,
  avg(case
        when line_clv is not null then case when line_clv > 0 then 1.0 when line_clv < 0 then 0.0 end
        when price_clv is not null then case when price_clv > 0 then 1.0 when price_clv < 0 then 0.0 end
      end) as pct_beat_close
from velocity.cumulative_record
where league != '__none__' and result in ('win','loss','push')
```

<BigValue data={clv} value=mean_line_clv title="Mean line CLV (pts)" fmt='+#,##0.00;-#,##0.00' />
<BigValue data={clv} value=pct_beat_close title="Beat the close" fmt='pct1' />
<BigValue data={clv} value=with_close title="Bets with a close" />

```sql clv_by_day
select slate_date, upper(league) as lg,
  avg(line_clv) as clv
from velocity.cumulative_record
where league != '__none__' and line_clv is not null
group by slate_date, league
order by slate_date
```

<LineChart
  data={clv_by_day}
  x=slate_date
  y=clv
  series=lg
  yAxisTitle="mean line CLV (pts) per day"
  emptySet=pass
  emptyMessage="CLV accrues once graded plays match an archived close (the hourly odds snapshots)."
/>

## Units over time

```sql units_by_day
select slate_date, league, units
from velocity.units
where league != '__none__'
order by slate_date
```

<LineChart
  data={units_by_day}
  x=slate_date
  y=units
  series=league
  yAxisTitle="cumulative units"
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
  coalesce(sum(profit), 0) as units
from velocity.cumulative_record
where league != '__none__' and result in ('win','loss','push')
group by league
order by units desc
```

<DataTable data={by_league} emptySet=pass emptyMessage="No graded plays yet.">
  <Column id=lg title="League" />
  <Column id=w title="W" />
  <Column id=l title="L" />
  <Column id=p title="P" />
  <Column id=units title="Units" fmt='+#,##0.0' contentType=delta />
</DataTable>

## Latest graded slate

```sql latest_graded
select upper(league) as lg, section, play, upper(side) as side, point, price,
  result, profit
from velocity.record
where league != '__none__' and result is not null
order by league, section
```

<DataTable data={latest_graded} rows=30 emptySet=pass emptyMessage="Yesterday's grading appears after the morning run.">
  <Column id=lg title="League" />
  <Column id=section title="Section" />
  <Column id=play title="Play" />
  <Column id=result title="Result" />
  <Column id=profit title="Profit" fmt='+#,##0.00' contentType=delta />
</DataTable>

_Losses shown as plainly as wins — the record is the product._
