---
title: Market health
---

The monitor's one-glance read: every market the record has settled, over the
trailing 7 and 30 days — the return on the stakes, the closing-line value where
the close is a yardstick, and what the model claimed against what it realized.
A flag is a question for the operator, not a verdict: exclusion needs the flag
to persist across two review windows.

```sql leagues
select '%' as league, 'All' as lg, 0 as ord
union all
select distinct league, upper(league), 1
from velocity.market_health
where league != '__none__'
order by ord, league
```

<ButtonGroup data={leagues} name=league value=league label=lg defaultValue="%" />

```sql flagged
select upper(league) as lg,
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'Moneyline'
    when 'team_total_home' then 'Team total (H)'
    when 'team_total_away' then 'Team total (A)'
    else market end as market_label,
  window_days, n_bets, roi, mean_line_clv, drift, flags
from velocity.market_health
where league != '__none__' and flags != '' and not thin
  and league like coalesce(nullif('${inputs.league}', ''), '%')
order by window_days, league, n_bets desc
```

<DataTable data={flagged} emptySet=pass emptyMessage="No market is flagged — every settled market with enough bets is inside its bands.">
  <Column id=lg title="League" />
  <Column id=market_label title="Market" />
  <Column id=window_days title="Window (days)" />
  <Column id=n_bets title="Bets" />
  <Column id=roi title="ROI" fmt='+#,##0.0%;-#,##0.0%' contentType=delta />
  <Column id=mean_line_clv title="Line CLV (pts)" fmt='+#,##0.00;-#,##0.00' contentType=delta />
  <Column id=drift title="Realized − claimed" fmt='+#,##0.00;-#,##0.00' contentType=delta />
  <Column id=flags title="Flags" />
</DataTable>

## Trailing 30 days

```sql long
select upper(league) as lg,
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'Moneyline'
    when 'team_total_home' then 'Team total (H)'
    when 'team_total_away' then 'Team total (A)'
    else market end as market_label,
  n_bets, staked, profit, roi,
  case when clv_trusted then mean_line_clv end as line_clv,
  case when clv_trusted then pct_beat_close end as beat_close,
  claimed, realized, drift,
  case when thin then 'thin' when flags != '' then flags
    when clv_trusted then 'clean (CLV)' else 'clean (P/L)' end as read,
  strftime(as_of, '%Y-%m-%d') as as_of
from velocity.market_health
where league != '__none__' and window_days = 30
  and league like coalesce(nullif('${inputs.league}', ''), '%')
order by league, thin, n_bets desc
```

<DataTable data={long} groupBy=lg emptySet=pass emptyMessage="The monitor fills as graded days accumulate on the season chain.">
  <Column id=market_label title="Market" />
  <Column id=n_bets title="Bets" />
  <Column id=staked title="Staked" fmt='#,##0.0"u"' />
  <Column id=profit title="Profit" fmt='+#,##0.00;-#,##0.00' contentType=delta />
  <Column id=roi title="ROI" fmt='+#,##0.0%;-#,##0.0%' contentType=delta />
  <Column id=line_clv title="Line CLV (pts)" fmt='+#,##0.00;-#,##0.00' contentType=delta />
  <Column id=beat_close title="Beat the close" fmt='pct0' />
  <Column id=claimed title="Claimed" fmt='0.00' />
  <Column id=realized title="Realized" fmt='0.00' />
  <Column id=read title="Read" />
</DataTable>

## Trailing 7 days

```sql short
select upper(league) as lg,
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'Moneyline'
    when 'team_total_home' then 'Team total (H)'
    when 'team_total_away' then 'Team total (A)'
    else market end as market_label,
  n_bets, profit, roi,
  case when clv_trusted then mean_line_clv end as line_clv,
  case when clv_trusted then pct_beat_close end as beat_close,
  drift,
  case when thin then 'thin' when flags != '' then flags
    when clv_trusted then 'clean (CLV)' else 'clean (P/L)' end as read
from velocity.market_health
where league != '__none__' and window_days = 7
  and league like coalesce(nullif('${inputs.league}', ''), '%')
order by league, thin, n_bets desc
```

<DataTable data={short} groupBy=lg emptySet=pass emptyMessage="Nothing settled in the last week.">
  <Column id=market_label title="Market" />
  <Column id=n_bets title="Bets" />
  <Column id=profit title="Profit" fmt='+#,##0.00;-#,##0.00' contentType=delta />
  <Column id=roi title="ROI" fmt='+#,##0.0%;-#,##0.0%' contentType=delta />
  <Column id=line_clv title="Line CLV (pts)" fmt='+#,##0.00;-#,##0.00' contentType=delta />
  <Column id=beat_close title="Beat the close" fmt='pct0' />
  <Column id=drift title="Realized − claimed" fmt='+#,##0.00;-#,##0.00' contentType=delta />
  <Column id=read title="Read" />
</DataTable>

## How to read it

- **CLV is the yardstick** on spreads, totals and moneylines: a market losing
  to its close is flagged *negative CLV* only where the test survives
  Benjamini–Hochberg across the window's markets, so a flag by chance is
  rare; an *unconfirmed* flag is a raw rejection that did not survive.
- **P/L is the read** on props and team totals, whose closes few sharps price:
  *negative ROI* is the same one-sided test on per-bet return, and an
  untrusted market with a confirmed negative ROI over 30 days is an
  *exclusion candidate* — the `total_bases` pattern.
- **Overclaims** means the model's mean claimed probability on decided bets
  ran ahead of the realized win rate by more than 0.05: the shrink or the
  anchoring weight has drifted from what the record earns.
- **Thin** is fewer than 20 bets in the window; nothing else is said.
