---
title: Methods
---

What's live in each league's model — promoted through the walk-forward lab
(docs/MODEL_LAB.md), never hand-tuned on the live board.

```sql config
select upper(league) as lg, label, detail
from velocity.model_config
order by league
```

<DataTable data={config} rows=60 groupBy=lg emptySet=pass emptyMessage="Transparency block unavailable in this build.">
  <Column id=label title="Component" />
  <Column id=detail title="Configuration" wrap=true />
</DataTable>

## Reading the numbers

- **Model %** — the Monte Carlo probability of the listed side (10,000 sims
  per game unless noted).
- **Fair %** — the de-vigged market probability (worst case across
  multiplicative, additive, Shin, and power methods).
- **Edge** — Model % minus Fair %. The bet gate requires positive EV at the
  posted price, not just a probability gap.
- **Tier** — the intelligence layer's conviction grade (A/B/C); X marks a
  veto. Conviction = 0.4·edge + 0.6·context.
- **Units** — profit at the recommended stake with the quarter-Kelly cap.
- The record grades **every** recommended play against final scores;
  pending plays stay pending until finals land. CLV grades against the
  closing line where closes are banked.
