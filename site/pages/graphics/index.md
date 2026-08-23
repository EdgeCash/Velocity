---
title: Graphics
hide_title: true
---

<HeroBand
  title="Card room"
  subtitle="One all-inclusive sheet per game — market vs model, the deep-dive table, and the simulated margin in a single postable graphic. Pick a league from the menu."
/>

```sql sheet_counts
select upper(league) as lg, count(*) as sheets
from velocity.cards
where league != '__none__' and kind in ('sheet', 'social')
group by league
order by league
```

<DataTable data={sheet_counts} emptySet=pass emptyMessage="Sheets publish with each live run.">
  <Column id=lg title="League" />
  <Column id=sheets title="Sheets today" />
</DataTable>

## Record card

```sql cards_record
select kind, league, file, away, home, caption
from velocity.cards
where league != '__none__' and kind = 'recordcard'
order by league, file
```

<CardGallery cards={cards_record} empty="No record card yet — it renders once a graded day settles." />
