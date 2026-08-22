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
where result in ('win','loss','push')
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

## Units over time

```sql units_by_day
select slate_date, league, units
from velocity.units
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
where result in ('win','loss','push')
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
where result is not null
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
