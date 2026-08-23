---
title: WNBA
hide_title: true
---

<HeroBand
  title="WNBA card room"
  subtitle="The all-inclusive game sheets from the latest WNBA run — tap to open full-size, save it, or copy the post text."
/>

```sql sheets
select kind, league, file, away, home, caption
from velocity.cards
where league = 'wnba' and kind in ('sheet', 'social', 'deepdive')
order by case kind when 'sheet' then 0 when 'social' then 1 else 2 end, file
```

<CardGallery cards={sheets} empty="No WNBA sheets in the latest run — they render with each live slate." />

## Sim checks

Yesterday's actual results on the pregame distributions.

```sql simchecks
select kind, league, file, away, home, caption
from velocity.cards
where league = 'wnba' and kind = 'simcheck'
order by file
```

<CardGallery cards={simchecks} empty="No WNBA sim checks yet — they render after grading." />
