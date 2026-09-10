---
title: Market health
sidebar_position: 5
hide_title: true
---

```sql asof
select strftime(max(as_of), '%b %-d') as day, count(*) as rows
from velocity.market_health where league != '__none__'
```

<PageHead
  title="Market health"
  subtitle="Every market the record has settled, over the trailing 7 and 30 days. A flag is a question, not a verdict: an exclusion needs the same flag in two review windows."
  stamp={asof[0]?.rows > 0 ? `through ${asof[0].day}` : ''}
  stampLabelText="window"
/>

```sql summary
select
  count(*) filter (window_days = 30 and not thin) as measured,
  count(*) filter (window_days = 30 and flags != '' and not thin) as flagged,
  count(*) filter (window_days = 30 and flag_exclusion) as candidates,
  count(*) filter (window_days = 30 and thin) as thin
from velocity.market_health
where league != '__none__'
```

<StatRow min="9rem">
  <StatCard label="Markets measured" value={summary[0]?.measured} dp={0} sub="30-day window" />
  <StatCard label="Flagged" value={summary[0]?.flagged} dp={0}
    accent={summary[0]?.flagged > 0 ? 'money' : 'plain'} sub="needs a look" />
  <StatCard label="Exclusion candidates" value={summary[0]?.candidates} dp={0}
    sub="confirmed 30-day loser" />
  <StatCard label="Too thin to judge" value={summary[0]?.thin} dp={0} sub="under 20 bets" />
</StatRow>

```sql leagues
select '%' as league, 'All' as lg, 0 as ord
union all
select distinct league, upper(league), 1
from velocity.market_health
where league != '__none__'
order by ord, league
```

```sql flagged
select upper(league) as lg,
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'Moneyline'
    when 'team_total_home' then 'Team total (H)'
    when 'team_total_away' then 'Team total (A)'
    else replace(market, '_', ' ') end as market_label,
  window_days || 'd' as window,
  n_bets, roi, mean_line_clv, drift, flags
from velocity.market_health
where league != '__none__' and flags != '' and not thin
  and league like coalesce(nullif('${inputs.league}', ''), '%')
order by window_days, league, n_bets desc
```

<SectionBar title="Flags" meta={summary[0]?.flagged > 0 ? 'act on these first' : 'nothing flagged'} />

{#if leagues.length > 1}

<ButtonGroup data={leagues} name=league value=league label=lg defaultValue="%" />

{/if}

<DataTable data={flagged} compact={true} rowShading={false} emptySet=pass
  emptyMessage="No market is flagged — every settled market with enough bets is inside its bands.">
  <Column id=lg title="Lg" />
  <Column id=market_label title="Market" />
  <Column id=window title="Window" align=center />
  <Column id=n_bets title="Bets" align=right />
  <Column id=roi title="ROI" fmt='+#,##0.0%;−#,##0.0%' align=right contentType=delta deltaSymbol={false} />
  <Column id=mean_line_clv title="Line CLV" fmt='+#,##0.00;−#,##0.00' align=right contentType=delta deltaSymbol={false} />
  <Column id=drift title="Real − claim" fmt='+#,##0.00;−#,##0.00' align=right contentType=delta deltaSymbol={false} />
  <Column id=flags title="Flag" wrap={true} />
</DataTable>

```sql long
select upper(league) as lg,
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'Moneyline'
    when 'team_total_home' then 'Team total (H)'
    when 'team_total_away' then 'Team total (A)'
    else replace(market, '_', ' ') end as market_label,
  n_bets, staked, profit, roi,
  case when clv_trusted then mean_line_clv end as line_clv,
  case when clv_trusted then pct_beat_close end as beat_close,
  claimed, realized,
  case when thin then 'thin' when flags != '' then flags
    when clv_trusted then 'clean' else 'clean' end as read
from velocity.market_health
where league != '__none__' and window_days = 30
  and league like coalesce(nullif('${inputs.league}', ''), '%')
order by league, thin, n_bets desc
```

<SectionBar title="Trailing 30 days" meta="the window that decides" />

<DataTable data={long} groupBy=lg compact={true} rowShading={false} emptySet=pass
  emptyMessage="The monitor fills as graded days accumulate on the season chain.">
  <Column id=market_label title="Market" />
  <Column id=n_bets title="Bets" align=right />
  <Column id=staked title="Staked" fmt='#,##0.0"u"' align=right />
  <Column id=profit title="Profit" fmt='+#,##0.00;−#,##0.00' align=right contentType=delta deltaSymbol={false} />
  <Column id=roi title="ROI" fmt='+#,##0.0%;−#,##0.0%' align=right contentType=delta deltaSymbol={false} />
  <Column id=line_clv title="Line CLV" fmt='+#,##0.00;−#,##0.00' align=right contentType=delta deltaSymbol={false} />
  <Column id=beat_close title="Beat close" fmt='0%' align=right />
  <Column id=claimed title="Claimed" fmt='0.00' align=right />
  <Column id=realized title="Realized" fmt='0.00' align=right />
  <Column id=read title="Read" align=center chip={true} />
</DataTable>

```sql short
select upper(league) as lg,
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'Moneyline'
    when 'team_total_home' then 'Team total (H)'
    when 'team_total_away' then 'Team total (A)'
    else replace(market, '_', ' ') end as market_label,
  n_bets, profit, roi,
  case when clv_trusted then mean_line_clv end as line_clv,
  case when thin then 'thin' when flags != '' then flags else 'clean' end as read
from velocity.market_health
where league != '__none__' and window_days = 7
  and league like coalesce(nullif('${inputs.league}', ''), '%')
order by league, thin, n_bets desc
```

<SectionBar title="Trailing 7 days" meta="the early warning" />

<DataTable data={short} groupBy=lg compact={true} rowShading={false} emptySet=pass
  emptyMessage="Nothing settled in the last week.">
  <Column id=market_label title="Market" />
  <Column id=n_bets title="Bets" align=right />
  <Column id=profit title="Profit" fmt='+#,##0.00;−#,##0.00' align=right contentType=delta deltaSymbol={false} />
  <Column id=roi title="ROI" fmt='+#,##0.0%;−#,##0.0%' align=right contentType=delta deltaSymbol={false} />
  <Column id=line_clv title="Line CLV" fmt='+#,##0.00;−#,##0.00' align=right contentType=delta deltaSymbol={false} />
  <Column id=read title="Read" align=center chip={true} />
</DataTable>

<SectionBar title="How to read it" />

- **CLV is the yardstick** on spreads, totals and moneylines. A market losing to
  its close is flagged *negative CLV* only where the test survives
  Benjamini–Hochberg across the window's markets, so a flag by chance is rare.
  An *unconfirmed* flag is a raw rejection that did not survive.
- **P/L is the read** on props and team totals, whose closes few sharps price.
  *Negative ROI* is the same one-sided test on per-bet return, and an untrusted
  market with a confirmed negative 30-day ROI is an *exclusion candidate*.
- **Overclaims** means the mean claimed probability on decided bets ran more
  than 0.05 ahead of the realized win rate: the shrink or the anchoring weight
  has drifted from what the record earns.
- **Thin** is fewer than 20 bets in the window. Nothing else is said about it.
