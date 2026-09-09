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
  upper(side) as side, price,
  stake_sized, stake as stake_solo,
  edge, conviction, context
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
  <Column id=stake_sized title="Stake" fmt='#,##0.00"u"' />
  <Column id=stake_solo title="Solo Kelly" fmt='#,##0.00"u"' />
  <Column id=edge title="Edge" fmt='pct1' contentType=delta />
  <Column id=conviction title="Conviction" fmt='#,##0.00' />
  <Column id=context title="Context" fmt='+#,##0.00;-#,##0.00' />
</DataTable>

_Stake is the portfolio-sized number — solo Kelly after the per-game,
per-class and slate caps, in units of a 100-unit bankroll. A published play
at a zero stake is a paper call: priced and graded, not wagered._

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
corroborates, a market that moved against us, a number past the
adverse-selection ceiling, or a market the model prices on paper only stays
here, visibly, instead of on the card.

<DataTable data={held_back} emptySet=pass emptyMessage="Nothing was held back — every candidate either published or never cleared the model's own gate.">
  <Column id=lg title="League" />
  <Column id=matchup title="Matchup" />
  <Column id=player title="Player" />
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=edge title="Edge" fmt='pct1' />
  <Column id=conviction title="Conviction" fmt='#,##0.00' />
  <Column id=reason title="Why it sat" wrap=true />
</DataTable>

## Parlays

```sql parlays
select
  upper(league) as lg,
  legs, n_legs, price, p_win, ev, stake,
  case when same_game then 'same game — EV is an upper bound' else '' end as caveat
from velocity.parlays
where league != '__none__'
order by ev desc
```

Combinations of staked legs whose joint probability, simulated on the same
game outcomes, clears the combined-EV bar at the product price. A same-game
combo is flagged: books reprice correlated legs, so its EV is the most it
could be, not what the book will pay.

<DataTable data={parlays} emptySet=pass emptyMessage="No parlay cleared the combined-EV bar today.">
  <Column id=lg title="League" />
  <Column id=legs title="Legs" wrap=true />
  <Column id=n_legs title="#" />
  <Column id=price title="Price" fmt='+#,##0;-#,##0' />
  <Column id=p_win title="Win %" fmt='pct1' />
  <Column id=ev title="EV" fmt='pct1' contentType=delta />
  <Column id=stake title="Stake" fmt='#,##0.00"u"' />
  <Column id=caveat title="Caveat" wrap=true />
</DataTable>

_The gate (docs/PUBLISH_GATE.md): tier-A conviction with a composite floor,
positive corroborating context (matchup/form/rest/injuries, SP+ agreement on
college, BettingPros agreement on props, stale-line demotion), an edge band
whose ceiling is the adverse-selection guard, no adverse line movement, and
a nightly cap. The full board — every bet the model itself would stake —
still lives on the [Today page](/) with tiers, edges and sized stakes._
