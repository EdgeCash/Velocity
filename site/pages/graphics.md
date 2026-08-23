---
title: Graphics
hide_title: true
---

<HeroBand
  title="Card room"
  subtitle="The rendered matchup graphics from the latest run — tap a card to open it full-size, save it, or copy the post text for social."
/>

```sql cards_social
select kind, league, file, away, home, caption
from velocity.cards
where league != '__none__' and kind = 'social'
order by league, file
```

```sql cards_deepdive
select kind, league, file, away, home, caption
from velocity.cards
where league != '__none__' and kind = 'deepdive'
order by league, file
```

```sql cards_simcheck
select kind, league, file, away, home, caption
from velocity.cards
where league != '__none__' and kind = 'simcheck'
order by league, file
```

```sql cards_record
select kind, league, file, away, home, caption
from velocity.cards
where league != '__none__' and kind = 'recordcard'
order by league, file
```

## Matchup cards

The pregame MARKET vs MODEL cards — built for posting.

<CardGallery cards={cards_social} empty="No matchup cards in the latest run — they render with each live slate." />

## Deep dives

The research page behind each card: form, unit splits, and the simulated margin.

<CardGallery cards={cards_deepdive} empty="No deep dives in the latest run." />

## Sim checks

Yesterday's actual results plotted on the pregame distributions.

<CardGallery cards={cards_simcheck} empty="No sim checks yet — they render after grading." />

## Record card

<CardGallery cards={cards_record} empty="No record card yet — it renders once a graded day settles." />
