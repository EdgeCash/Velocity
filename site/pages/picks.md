---
title: Plays
---

The plays are the promise: every candidate below cleared the model's edge
gate, was judged by the intel layer (matchup, form, rest, injuries, outside
systems), and then faced the publish gate — a conviction floor, a
corroboration requirement, an edge band with an adverse-selection ceiling,
and a no-adverse-line-move check, capped at the highest-conviction few.
**No picks is a pick**: a quiet day means the gate did its job.

```sql plays
select
  upper(league) as lg,
  away_team || ' @ ' || home_team as matchup,
  '/matchup/' || game_id as matchup_link,
  player,
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'ML'
    when 'team_total_home' then 'Team total (H)'
    when 'team_total_away' then 'Team total (A)'
    when 'pass_yards' then 'Pass yards' when 'pass_tds' then 'Pass TDs'
    when 'rush_yards' then 'Rush yards'
    when 'receiving_yards' then 'Rec yards'
    when 'receptions' then 'Receptions'
    when 'pitcher_strikeouts' then 'Pitcher Ks'
    else market end as market_label,
  upper(side) as side, price, stake, edge, conviction, context
from velocity.publish
where league != '__none__' and published
order by conviction desc
```

<DataTable data={plays} link=matchup_link emptySet=pass emptyMessage="No plays today — nothing cleared the gate. That is the product working, not the product missing.">
  <Column id=lg title="League" />
  <Column id=matchup title="Matchup" />
  <Column id=player title="Player" />
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=price title="Price" fmt='+#,##0;-#,##0' />
  <Column id=stake title="Stake" fmt='"$"#,##0.00' />
  <Column id=edge title="Edge" fmt='pct1' contentType=delta />
  <Column id=conviction title="Conviction" fmt='#,##0.00' />
  <Column id=context title="Context" fmt='+#,##0.00;-#,##0.00' />
</DataTable>

```sql held_back
select
  upper(league) as lg,
  away_team || ' @ ' || home_team as matchup,
  player,
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'ML'
    when 'team_total_home' then 'Team total (H)'
    when 'team_total_away' then 'Team total (A)'
    when 'pass_yards' then 'Pass yards' when 'pass_tds' then 'Pass TDs'
    when 'rush_yards' then 'Rush yards'
    when 'receiving_yards' then 'Rec yards'
    when 'receptions' then 'Receptions'
    when 'pitcher_strikeouts' then 'Pitcher Ks'
    else market end as market_label,
  upper(side) as side, edge, conviction, reason
from velocity.publish
where league != '__none__' and not published
order by conviction desc
```

## Held back — and why

Every other candidate the model liked, with the exact rule that stopped it.
This is the discipline behind the short list: a big edge nothing
corroborates, a market that moved against us, or a number past the
adverse-selection ceiling stays here, visibly, instead of on the card.

<DataTable data={held_back} emptySet=pass emptyMessage="Nothing was held back — every candidate either published or never cleared the model's own gate.">
  <Column id=lg title="League" />
  <Column id=matchup title="Matchup" />
  <Column id=player title="Player" />
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=edge title="Edge" fmt='pct1' />
  <Column id=conviction title="Conviction" fmt='#,##0.00' />
  <Column id=reason title="Why it sat" />
</DataTable>

_The gate (docs/PUBLISH_GATE.md): tier-A conviction with a composite floor,
positive corroborating context (matchup/form/rest/injuries, SP+ agreement on
college, BettingPros agreement on props, stale-line demotion), an edge band
whose ceiling is the adverse-selection guard, no adverse line movement, and
a nightly cap. The full board — every bet the model itself would stake —
still lives on the [Today page](/) with tiers and edges._
