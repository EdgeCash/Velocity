---
title: Methods
sidebar_position: 7
hide_title: true
---

<PageHead
  title="Methods"
  subtitle="What is live in each league's model, written by the run itself from the flags it actually used — so this page cannot drift from the code."
/>

What's live in each league's model — promoted through the walk-forward lab
(docs/MODEL_LAB.md), never hand-tuned on the live board. Each block is
written by the run itself from the flags it actually ran with, so this page
cannot drift from the code.

```sql config
select upper(league) as lg, label, detail
from velocity.model_config
where league != '__none__'
order by league
```

<DataTable data={config} rows=60 groupBy=lg emptySet=pass emptyMessage="Transparency block unavailable in this build.">
  <Column id=label title="Component" />
  <Column id=detail title="Configuration" wrap=true />
</DataTable>

## Reading the numbers

- **Model %** — the Monte Carlo probability of the listed side (10,000 sims
  per game unless noted), after the league's market anchoring above.
- **Fair %** — the de-vigged market probability; the De-vig row above says
  which anchor (the cross-book consensus, or the shopped book's own pair).
- **Edge** — Model % minus Fair %. The bet gate requires positive EV at the
  shopped price, not just a probability gap; past the edge ceilings the row
  turns to paper.
- **Tier** — the intelligence layer's conviction grade (A/B/C); X marks a
  veto. Conviction = 0.4·edge + 0.6·context.
- **Stake** — the portfolio-sized number: quarter-Kelly, then the per-bet,
  per-game, per-class and slate caps, in units of the ledger's bankroll
  (100 units until a ledger exists). The solo-Kelly stake the slate keeps
  for backtest comparability is shown alongside where it matters.
- **Bankroll** — the ledger's: seeded once, moved only by settled bets and
  recorded adjustments, never reset per run. Money already on the table
  counts against the slate cap, a bet already on the books is held rather
  than re-placed, and a drawdown of 30% from the peak halts the whole card
  (the kill-switch — explicit on the Today page when it trips). Bets reach
  the ledger either automatically at the recommended terms or as the
  operator records them; the Performance page says which.
- **Paper** — a priced row with no stake: a market the record has not yet
  earned (team totals; every market in a league still proving out), or an
  edge past a ceiling. Paper rows are graded and carry CLV; they never
  enter the units.
- **Units** — profit at the sized stake (solo where the chain predates
  sizing). The record grades **every** priced play against final scores;
  pending plays stay pending until finals land. CLV grades against the
  closing line where closes are banked, and is read only on markets whose
  close is sharp (spreads, totals, moneylines).
