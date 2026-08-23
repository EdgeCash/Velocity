---
title: Picks
---

```sql picks
select
  upper(league) as lg,
  away_team || ' @ ' || home_team as matchup,
  '/matchup/' || game_id as matchup_link,
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'ML' else market end as market_label,
  upper(side) as side, point, price, stake, edge, tier, conviction
from velocity.board
where league != '__none__' and tier is not null and tier != 'X'
order by conviction desc
```

<DataTable data={picks} link=matchup_link emptySet=pass emptyMessage="No tiered picks today — the intel layer found nothing above the bar.">
  <Column id=tier title="Tier" />
  <Column id=lg title="League" />
  <Column id=matchup title="Matchup" />
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=point title="Line" fmt='#,##0.0' />
  <Column id=price title="Price" fmt='+#,##0' />
  <Column id=stake title="Stake" fmt='"$"#,##0.00' />
  <Column id=edge title="Edge" fmt='pct1' contentType=delta />
</DataTable>

```sql vetoed
select upper(league) as lg,
  away_team || ' @ ' || home_team as matchup,
  case market when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'ML' else market end as market_label,
  upper(side) as side, edge
from velocity.board
where league != '__none__' and tier = 'X'
order by edge desc
```

## Vetoed by the intel layer

<DataTable data={vetoed} emptySet=pass emptyMessage="Nothing vetoed today.">
  <Column id=lg title="League" />
  <Column id=matchup title="Matchup" />
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=edge title="Edge (before veto)" fmt='pct1' />
</DataTable>

_Tiers come from the intelligence layer (docs/INTEL.md): conviction blends
market edge with matchup/form/rest/injury context; X = vetoed — the one
channel the backtest measured as evidence-positive._
