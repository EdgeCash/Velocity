---
title: Ratings
hide_title: true
---

<HeroBand
  title="Power ratings"
  subtitle="The per-team strengths behind the live model, straight from each league's promoted fit — never hand-tuned. Off/Def are deviations from league average; Net is the expected margin against an average opponent on a neutral floor."
/>

```sql ratings_rows
select
  upper(league) as lg,
  rank,
  team,
  net,
  off,
  "def" as defense,
  pace,
  scale,
  case when rank_prev is not null then rank_prev - rank end as moved,
  case when net_prev is not null then round(net - net_prev, 2) end as net_chg
from velocity.ratings
where league != '__none__'
order by league, rank
```

<DataTable data={ratings_rows} rows=60 groupBy=lg search=true emptySet=pass emptyMessage="Ratings publish with each live run — the table fills when a slate prices.">
  <Column id=rank title="#" />
  <Column id=team title="Team" />
  <Column id=net title="Net" fmt='+#,##0.00;-#,##0.00' contentType=delta />
  <Column id=off title="Off" fmt='+#,##0.00;-#,##0.00' />
  <Column id=defense title="Def" fmt='+#,##0.00;-#,##0.00' />
  <Column id=pace title="Pace" fmt='#,##0.0' />
  <Column id=moved title="Δ rank" fmt='+#,##0;-#,##0' contentType=delta />
  <Column id=net_chg title="Δ net" fmt='+#,##0.00;-#,##0.00' contentType=delta />
</DataTable>

_Scales differ by league (each fit's natural unit): points/game for the
football fits, runs/game for MLB (with the starter effect decomposed out),
and points per 100 possessions for the basketball leagues — where **Pace**
is the team's expected possessions in a game against an average opponent.
**Def** is points allowed vs average, so negative is good. Δ columns track
movement since the previous run's export._
