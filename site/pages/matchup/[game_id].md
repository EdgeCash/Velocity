```sql game
select away_team || ' @ ' || home_team as matchup, kickoff, upper(league) as lg
from velocity.games
where game_id = '${params.game_id}'
limit 1
```

<PageHead
  title={game[0]?.matchup ?? 'Matchup'}
  subtitle={game[0]?.lg ? game[0].lg + ' · every price, the simulated distribution, and the argument' : ''}
/>

```sql proj
select mu_away, mu_home, p_home_win, fair_spread, fair_total, n_sims
from velocity.projections
where game_id = '${params.game_id}'
union all
select null, null, null, null, null, null
where not exists (select 1 from velocity.projections
                  where game_id = '${params.game_id}')
limit 1
```

<BigValue data={proj} value=mu_away title="Projected away" fmt='#,##0.0' />
<BigValue data={proj} value=mu_home title="Projected home" fmt='#,##0.0' />
<BigValue data={proj} value=p_home_win title="Home win %" fmt='pct1' />
<BigValue data={proj} value=fair_total title="Fair total" fmt='#,##0.0' />

```sql conditions
select w.covered, w.temp_f, w.wind_mph, w.precip_pct
from velocity.weather w
where w.game_id = '${params.game_id}'
limit 1
```

<WeatherLine row={conditions[0]} />

## Line movement

```sql moves
select
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'Moneyline' else market end as market_label,
  upper(side) as side,
  point_open, price_open, point_now, price_now,
  case when market != 'moneyline' then point_now - point_open end as pt_move
from velocity.line_moves
where game_id = '${params.game_id}'
order by market, side
```

<DataTable data={moves} emptySet=pass emptyMessage="Movement appears once the hourly odds archive has seen this game more than once.">
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=point_open title="Open" fmt='#,##0.0' />
  <Column id=price_open title="Open price" fmt='+0;−0' />
  <Column id=point_now title="Now" fmt='#,##0.0' />
  <Column id=price_now title="Now price" fmt='+0;−0' />
  <Column id=pt_move title="Move" fmt='+#,##0.0;−#,##0.0' contentType=delta deltaSymbol={false} />
</DataTable>

## Markets

```sql markets
select
  game_id, league, market, side, point, price, book, venue,
  p_model, p_fair, edge, tier, conviction, rationale, note,
  coalesce(stake_sized, stake) as stake
from velocity.board
where game_id = '${params.game_id}'
order by case when coalesce(stake_sized, 0) > 0 then 0 else 1 end, edge desc
```

```sql refusals
select
  count(*) filter (note is not null) as paper,
  count(*) filter (stake_sized > 0) as staked,
  sum(stake_sized) as exposure
from velocity.board
where game_id = '${params.game_id}'
```

```sql game_dist
select game_id, kind, value, prob
from velocity.distributions
where game_id = '${params.game_id}'
```

{#if markets.length > 0}
  <div class="market-cards">
    {#each markets as m, i}
      <PlayCard {...m} lead={i === 0 && m.stake > 0} dist={game_dist} compact={true} />
    {/each}
  </div>
{:else}
  <EmptyNote
    title="No priced market for this game"
    detail="The board carries a market only where the model could price it and a book was quoting it. A game with neither shows nothing here rather than an empty table."
  />
{/if}

<style>
  /* One column, then two once there is genuinely room. `auto-fill` with a
     fixed minimum cannot shrink below that minimum, so at phone width it
     overflowed the content column and clipped every card's price — the
     same shape of bug that lost the board's last columns. */
  .market-cards {
    display: grid;
    gap: 0.7rem;
    margin: 0.6rem 0 0.4rem;
  }
  @media (min-width: 1000px) {
    .market-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
</style>

_{refusals[0]?.staked ?? 0} market(s) staked here for
{(refusals[0]?.exposure ?? 0).toFixed(2)}u after the per-game cap;
{refusals[0]?.paper ?? 0} priced on paper (a ceiling or an untrusted market —
the reason rides on the card)._

## The argument

```sql argued
select
  case market
    when 'spread' then 'Spread' when 'total' then 'Total'
    when 'moneyline' then 'Moneyline'
    when 'team_total_home' then 'Team total (home)'
    when 'team_total_away' then 'Team total (away)'
    else market end as market_label,
  upper(side) as side, coalesce(tier, '') as tier, rationale
from velocity.board
where game_id = '${params.game_id}'
  and rationale is not null and rationale != ''
order by case when stake_sized > 0 then 0 else 1 end, edge desc
```

The intel layer's case for and against each priced side — matchup, form,
rest, injuries, outside systems. It annotates and vetoes; it never promotes
a bet the model did not already like.

<DataTable data={argued} emptySet=pass emptyMessage="No intel annotations for this game.">
  <Column id=market_label title="Market" />
  <Column id=side title="Side" />
  <Column id=tier title="Tier" />
  <Column id=rationale title="Rationale" wrap=true />
</DataTable>

## Simulated total

```sql market_lines
select
  max(case when market = 'total' then point end) as totalline,
  max(case when market = 'spread' and side = 'home' then -point end) as marginline
from velocity.board
where game_id = '${params.game_id}'
```

```sql total_pmf
select value, prob
from velocity.distributions
where game_id = '${params.game_id}' and kind = 'total'
order by value
```

<BarChart
  data={total_pmf}
  x=value
  y=prob
  xAxisTitle="total points"
  yAxisTitle="probability"
  fillColor="#2bb3ab"
  emptySet=pass
  emptyMessage="No distribution banked for this game."
>
  {#if market_lines[0]?.totalline}
    <ReferenceLine x={market_lines[0].totalline} label="market total" color=warning />
  {/if}
</BarChart>

A distribution without the number drawn on it is decoration. The rule is where
the book has the total; the model's opinion is how much of the mass sits past
it. That mass is the **raw simulation** — the belief the board stakes is the
same number pulled four fifths of the way back to the market
([Methods](/methods)).

## Simulated margin

```sql margin_pmf
select value, prob
from velocity.distributions
where game_id = '${params.game_id}' and kind = 'margin'
order by value
```

<BarChart
  data={margin_pmf}
  x=value
  y=prob
  xAxisTitle="home margin"
  yAxisTitle="probability"
  fillColor="#2bb3ab"
  emptySet=pass
  emptyMessage="No distribution banked for this game."
>
  {#if market_lines[0]?.marginline !== null && market_lines[0]?.marginline !== undefined}
    <ReferenceLine x={market_lines[0].marginline} label="spread" color=warning />
  {/if}
  <ReferenceLine x={0} label="even" color=info />
</BarChart>

## Injury report

```sql inj
select i.team, i.player_name, i.position, i.status, i.is_out
from velocity.injuries i
join velocity.projections p
  on p.game_id = '${params.game_id}'
 and (i.team = p.away or i.team = p.home)
where i.league != '__none__'
order by i.team, i.is_out desc, i.player_name
```

<DataTable data={inj} rows=30 groupBy=team emptySet=pass emptyMessage="No banked injury designations for these teams (the daily snapshot covers NFL).">
  <Column id=player_name title="Player" />
  <Column id=position title="Pos" />
  <Column id=status title="Status" />
</DataTable>

## Cards

The rendered graphics for this game — tap to open full-size, save to post.

```sql game_cards
select kind, league, file, away, home, caption
from velocity.cards
where league != '__none__' and game_id = '${params.game_id}'
order by case kind when 'sheet' then 0 when 'social' then 1
  when 'deepdive' then 2 else 3 end
```

<CardGallery cards={game_cards} empty="No cards rendered for this game yet — they publish with each run." />
