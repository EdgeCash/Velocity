---
title: DFS
---

```sql lineup
select upper(league) as lg,
  coalesce(nullif(slate, ''), 'Main slate') as slate_name,
  slot, player_name, position, team, game_time,
  salary, points,
  case when salary > 0 then points / (salary / 1000.0) end as value,
  slate_start
from velocity.dfs_lineup
where league != '__none__'
order by league, slate_start nulls last, salary desc
```

## Cash optimal — every slate grouping

One optimal lineup per classic slate DK posted (main, early, night, turbo),
each solved on its own board.

<DataTable data={lineup} groupBy=slate_name emptySet=pass emptyMessage="No DFS lineup banked — the card builds when DK salaries and projections are both live.">
  <Column id=lg title="League" />
  <Column id=slot title="Slot" />
  <Column id=player_name title="Player" />
  <Column id=team title="Team" />
  <Column id=game_time title="Game (CT)" />
  <Column id=salary title="Salary" fmt='"$"#,##0' />
  <Column id=points title="Proj" fmt='#,##0.0' />
  <Column id=value title="Value" fmt='#,##0.00"x"' />
</DataTable>

```sql lineup_total
select upper(league) as lg,
  coalesce(nullif(slate, ''), 'Main slate') as slate_name,
  sum(salary) as total_salary, sum(points) as total_points
from velocity.dfs_lineup
where league != '__none__'
group by league, slate_name, slate_start
order by slate_start nulls last
```

<DataTable data={lineup_total} emptySet=pass emptyMessage=" ">
  <Column id=lg title="League" />
  <Column id=slate_name title="Slate" />
  <Column id=total_salary title="Total salary" fmt='"$"#,##0' />
  <Column id=total_points title="Total proj" fmt='#,##0.1' />
</DataTable>

## Showdown — single game, captain at 1.5x

```sql showdown
select upper(league) as lg,
  coalesce(nullif(slate, ''), 'Showdown') as game_name,
  slot, player_name, position, team, game_time, salary, points,
  case when salary > 0 then points / (salary / 1000.0) end as value,
  slate_start
from velocity.dfs_showdown
where league != '__none__'
order by league, slate_start nulls last, points desc
```

The captain scores **and** costs 1.5x, and DK requires at least two teams on
the roster — the salary and projection below are already the captain's
inflated numbers.

<DataTable data={showdown} groupBy=game_name emptySet=pass emptyMessage="No showdown board solved — DK posts them for single games only.">
  <Column id=lg title="League" />
  <Column id=slot title="Slot" />
  <Column id=player_name title="Player" />
  <Column id=team title="Team" />
  <Column id=game_time title="Game (CT)" />
  <Column id=salary title="Salary" fmt='"$"#,##0' />
  <Column id=points title="Proj" fmt='#,##0.0' />
  <Column id=value title="Value" fmt='#,##0.00"x"' />
</DataTable>

## Tiers and Single Stat — no salary cap

```sql tiered
select upper(league) as lg, game_type, slot, player_name, team, game_time,
  points, unit
from velocity.dfs_tiered
where league != '__none__'
order by league, game_type, slot, points desc
```

No cap means no optimizer: DK hands you a pool (or six tiers of one) and the
projection is the whole contest. Points are in each contest's **own** unit —
home runs, touchdowns, or DK points.

<DataTable data={tiered} groupBy=game_type emptySet=pass emptyMessage="No Tiers or Single Stat entry banked.">
  <Column id=lg title="League" />
  <Column id=slot title="Slot" />
  <Column id=player_name title="Player" />
  <Column id=team title="Team" />
  <Column id=game_time title="Game (CT)" />
  <Column id=points title="Proj" fmt='#,##0.000' />
  <Column id=unit title="Unit" />
</DataTable>

## GPP set

```sql gpp
select upper(league) as lg, rank, stacks, total_points, total_salary, score, players
from velocity.dfs_gpp
where league != '__none__'
order by league, rank
```

<DataTable data={gpp} rows=20 emptySet=pass emptyMessage="GPP lineups appear alongside the cash card.">
  <Column id=lg title="League" />
  <Column id=rank title="#" />
  <Column id=stacks title="Stacks" />
  <Column id=total_points title="Proj" fmt='#,##0.1' />
  <Column id=total_salary title="Salary" fmt='"$"#,##0' />
  <Column id=players title="Players" wrap=true />
</DataTable>

_Salaries from DraftKings' public draftables feed; the optimizer honors DK's
roster rules per sport (docs/DASHBOARD_RESEARCH.md §8)._
