---
title: DFS
---

```sql lineup
select upper(league) as lg, slot, player_name, position, team, salary, points,
  case when salary > 0 then points / (salary / 1000.0) end as value
from velocity.dfs_lineup
order by league, salary desc
```

## Cash optimal

<DataTable data={lineup} emptySet=pass emptyMessage="No DFS lineup banked — the card builds when DK salaries and projections are both live.">
  <Column id=lg title="League" />
  <Column id=slot title="Slot" />
  <Column id=player_name title="Player" />
  <Column id=team title="Team" />
  <Column id=salary title="Salary" fmt='"$"#,##0' />
  <Column id=points title="Proj" fmt='#,##0.0' />
  <Column id=value title="Value" fmt='#,##0.00"x"' />
</DataTable>

```sql lineup_total
select upper(league) as lg, sum(salary) as total_salary, sum(points) as total_points
from velocity.dfs_lineup
group by league
```

<DataTable data={lineup_total} emptySet=pass emptyMessage=" ">
  <Column id=lg title="League" />
  <Column id=total_salary title="Total salary" fmt='"$"#,##0' />
  <Column id=total_points title="Total proj" fmt='#,##0.1' />
</DataTable>

## GPP set

```sql gpp
select upper(league) as lg, rank, stacks, total_points, total_salary, score, players
from velocity.dfs_gpp
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
