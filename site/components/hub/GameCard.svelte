<script>
  // One game: the row, and everything about that game underneath it.
  //
  // This is the component the rebuild exists for. The old site put a game's
  // projection, its prices, its DFS players and your position on it across
  // four pages; here the row IS the projection and opening it brings the rest
  // in place, with no navigation and nothing to re-find when you close it.
  import TeamMark from '../TeamMark.svelte';
  import Spark from './Spark.svelte';
  import BoxScore from './BoxScore.svelte';
  import {
    american, isNum, kickoffLabel, marketLabel, num, pct, signed,
    sideLabel, teamMark, venueColor, venueMark,
  } from '../format.js';
  import { impliedProb, ratingRows } from './model.js';

  export let game;
  export let identity = {};
  /** `{margin: [...], total: [...]}` for this game, already filtered. */
  export let dists = { margin: [], total: [] };
  export let open = false;
  export let isPrivate = true;
  /** `league|market` → the monitor's flagged health row, from `flaggedMarkets`. */
  export let flagged = new Map();
  export let onToggle = () => {};

  // The strongest form market health can take: not a table you go and read,
  // but a mark on the market itself, at the moment you are looking at it. The
  // monitor's own framing is preserved — a flag is a question, not a verdict —
  // so this says what is flagged and leaves the decision alone.
  const flagFor = (league, market) => flagged.get(`${league}|${market}`) ?? null;

  $: home = teamMark(identity, game.home_team, game.league);
  $: away = teamMark(identity, game.away_team, game.league);
  $: score = game.score;
  $: isLive = score?.state === 'in';
  $: isFinal = score?.state === 'post';
  $: proj = game.proj;

  // The model's own line, phrased the way a board phrases it: the favourite
  // and the number, not a signed margin the reader has to interpret.
  $: modelLine = (() => {
    if (!proj) return null;
    const spread = Number(proj.fair_spread);
    if (!Number.isFinite(spread)) return null;
    const homeFav = spread < 0;
    const side = homeFav ? home.code : away.code;
    return { side, number: signed(Math.abs(spread) * -1, 1).replace('.0', '') };
  })();

  $: topMarket = game.markets[0] ?? null;
  $: bestEdge = game.markets.reduce(
    (best, m) => {
      const edge = Number(m.best?.edge);
      return Number.isFinite(edge) && edge > best ? edge : best;
    },
    -Infinity,
  );

  function marketPhrase(market) {
    const kind = String(market.market ?? '');
    const side = String(market.side ?? '');
    const point = market.point;
    if (kind === 'moneyline') {
      return side === 'home' ? home.code : away.code;
    }
    if (kind === 'spread') {
      const team = side === 'home' ? home.code : away.code;
      return `${team} ${signed(point, 1).replace('.0', '')}`;
    }
    if (kind === 'total') {
      return `${sideLabel(side).slice(0, 1)} ${num(point, 1).replace('.0', '')}`;
    }
    if (kind.startsWith('team_total')) {
      const team = kind.endsWith('home') ? home.code : away.code;
      return `${team} ${sideLabel(side).slice(0, 1)} ${num(point, 1).replace('.0', '')}`;
    }
    return `${marketLabel(kind)} ${sideLabel(side)}`;
  }

  /** The distribution a market cuts, and where. Nothing for a team total. */
  function markFor(market) {
    const kind = String(market?.market ?? '');
    const side = String(market?.side ?? '');
    const point = Number(market?.point);
    if (kind === 'moneyline') return { kind: 'margin', at: 0 };
    if (!Number.isFinite(point)) return null;
    if (kind === 'total') return { kind: 'total', at: point };
    if (kind === 'spread') return { kind: 'margin', at: side === 'home' ? -point : point };
    return null;
  }

  $: headMark = markFor(topMarket);

  $: h2h = ratingRows(game.ratings?.away, game.ratings?.home);
  // Every league's fit has its own natural unit — points per game for the
  // football fits, runs per game for baseball, points per 100 possessions for
  // basketball — so the block has to say which one these numbers are in.
  $: scale = String(game.ratings?.home?.scale ?? game.ratings?.away?.scale ?? '');
</script>

<article class="card" class:open class:live={isLive}>
  <!-- The whole row is the control. A chevron-only target is a miss on a
       phone, and this is a surface people use one-handed. -->
  <button
    class="row"
    aria-expanded={open}
    on:click={() => onToggle(game.game_id)}
  >
    <span class="lg">{game.league.toUpperCase()}</span>

    <span class="teams">
      <span class="side">
        <TeamMark code={away.code} logo={away.logo} color={away.color}
                  label={game.away_team} size={22} />
        <span class="name">{game.away_team}</span>
        {#if score && score.state !== 'pre'}
          <span class="sc" class:win={score.away_score > score.home_score}>
            {score.away_score}
          </span>
        {/if}
      </span>
      <span class="side">
        <TeamMark code={home.code} logo={home.logo} color={home.color}
                  label={game.home_team} size={22} />
        <span class="name">{game.home_team}</span>
        {#if score && score.state !== 'pre'}
          <span class="sc" class:win={score.home_score > score.away_score}>
            {score.home_score}
          </span>
        {/if}
      </span>
    </span>

    <span class="state">
      {#if isLive}
        <span class="pill live"><span class="dot"></span>{score.detail}</span>
      {:else if isFinal}
        <span class="pill final">{score.detail || 'Final'}</span>
      {:else}
        <span class="when">{kickoffLabel(game.kickoff)}</span>
      {/if}
    </span>

    <span class="model">
      {#if modelLine}
        <span class="mline">{modelLine.side} {modelLine.number}</span>
        <span class="mtotal">{num(proj.fair_total, 1).replace('.0', '')}</span>
      {:else}
        <span class="mnone">not priced</span>
      {/if}
    </span>

    <span class="flags">
      {#if game.positions.length}
        <span class="flag pos" title="you have a position on this game">
          {game.positions.length}&nbsp;open
        </span>
      {/if}
      {#if isPrivate && game.staked > 0}
        <span class="flag staked">{num(game.staked, 2)}u</span>
      {/if}
      {#if game.props.length}
        <span class="flag prop">{game.props.length}&nbsp;props</span>
      {/if}
      {#if game.dfs.length}
        <span class="flag dfs">{game.dfs.length}&nbsp;DFS</span>
      {/if}
      {#if game.n_markets}
        <span class="flag mk">{game.n_markets}&nbsp;mkt</span>
      {/if}
      <span class="chev" aria-hidden="true">{open ? '−' : '+'}</span>
    </span>
  </button>

  {#if open}
    <div class="sheet">
      <!-- ---- the projection ------------------------------------------- -->
      <section class="proj">
        <h4>Model</h4>
        {#if proj}
          <div class="projgrid">
            <div class="stat">
              <span class="lab">{away.code}</span>
              <span class="val">{num(proj.mu_away, 1)}</span>
            </div>
            <div class="stat">
              <span class="lab">{home.code}</span>
              <span class="val">{num(proj.mu_home, 1)}</span>
            </div>
            <div class="stat">
              <span class="lab">{home.code} win</span>
              <span class="val">{pct(proj.p_home_win, 1)}</span>
            </div>
            <div class="stat">
              <span class="lab">Spread</span>
              <span class="val">{signed(proj.fair_spread, 1).replace('.0', '')}</span>
            </div>
            <div class="stat">
              <span class="lab">Total</span>
              <span class="val">{num(proj.fair_total, 1).replace('.0', '')}</span>
            </div>
            <div class="stat">
              <span class="lab">Sims</span>
              <span class="val">{Number(proj.n_sims ?? 0).toLocaleString()}</span>
            </div>
          </div>

          <div class="sparks">
            {#if dists.margin?.length}
              <figure>
                <Spark rows={dists.margin} mark={headMark?.kind === 'margin' ? headMark.at : null}
                       label="simulated margin" />
                <figcaption>
                  Margin ({home.code} − {away.code})
                  {#if headMark?.kind === 'margin'}
                    <span class="cut">line at {num(headMark.at, 1).replace('.0', '')}</span>
                  {/if}
                </figcaption>
              </figure>
            {/if}
            {#if dists.total?.length}
              <figure>
                <Spark rows={dists.total} mark={headMark?.kind === 'total' ? headMark.at : null}
                       label="simulated total" />
                <figcaption>
                  Total
                  {#if headMark?.kind === 'total'}
                    <span class="cut">line at {num(headMark.at, 1).replace('.0', '')}</span>
                  {/if}
                </figcaption>
              </figure>
            {/if}
          </div>
        {:else}
          <p class="empty">No projection for this game on the current build.</p>
        {/if}
      </section>

      <!-- ---- the head-to-head ------------------------------------------
           The ratings behind the projection, mirrored with the advantage
           marked down the middle. A list of markets says what the model
           thinks; this says why — and it is the one thing the old site's
           matchup page did that a list of numbers cannot. -->
      {#if h2h.length}
        <section class="h2h">
          <h4>
            Power ratings
            {#if scale}<span class="sub">{scale}</span>{/if}
          </h4>
          <div class="h2hgrid">
            <span class="hcorner"></span>
            <span class="hteam">
              <TeamMark code={away.code} logo={away.logo} color={away.color}
                        label={game.away_team} size={18} />
              {away.code}
            </span>
            <span class="hteam right">
              {home.code}
              <TeamMark code={home.code} logo={home.logo} color={home.color}
                        label={game.home_team} size={18} />
            </span>
            {#each h2h as row (row.key)}
              <span class="hlab">{row.label}</span>
              <span class="hval" class:lead={row.edge === 'away'}>
                {row.key === 'rank' ? `#${num(row.away, 0)}` : signed(row.away, row.dp)}
                {#if row.edge === 'away'}<span class="harrow" aria-label="advantage">◂</span>{/if}
              </span>
              <span class="hval right" class:lead={row.edge === 'home'}>
                {#if row.edge === 'home'}<span class="harrow" aria-label="advantage">▸</span>{/if}
                {row.key === 'rank' ? `#${num(row.home, 0)}` : signed(row.home, row.dp)}
              </span>
            {/each}
          </div>
          <p class="h2hnote">
            Off and Def are deviations from league average, so a
            <strong>negative Def is the good one</strong>; Net is the expected
            margin against an average opponent on a neutral floor. Pace is
            context, not an advantage, and is left unmarked.
          </p>
        </section>
      {/if}

      <!-- ---- the markets ---------------------------------------------- -->
      {#if game.markets.length}
        <section class="markets">
          <h4>
            Markets
            {#if isPrivate && Number.isFinite(bestEdge)}
              <span class="sub">best edge {pct(bestEdge, 1, true)}</span>
            {/if}
          </h4>
          <div class="mlist">
            {#each game.markets as m (m.key)}
              <div class="m">
                <div class="mhead">
                  <span class="mname">{marketPhrase(m)}</span>
                  <span class="mkind">{marketLabel(m.market)}</span>
                  {#if m.tier}<span class="tier t{m.tier}">{m.tier}</span>{/if}
                  {#if isPrivate}
                    {@const flag = flagFor(game.league, m.market)}
                    {#if flag}
                      <span
                        class="health"
                        class:bad={flag.flag_exclusion}
                        title={`${flag.n_bets} bets over ${flag.window_days} days — see Market health under Record`}
                      >
                        {flag.flags}
                      </span>
                    {/if}
                  {/if}
                </div>

                <div class="mnums">
                  <span class="pair">
                    <span class="k">Model</span>
                    <span class="v">{pct(m.p_model, 1)}</span>
                  </span>
                  {#if isPrivate && m.best}
                    <span class="pair">
                      <span class="k">Market</span>
                      <span class="v">{pct(impliedProb(m.best.price), 1)}</span>
                    </span>
                    <span class="pair">
                      <span class="k">Best</span>
                      <span class="v">{american(m.best.price)}</span>
                    </span>
                    {#if isNum(m.best.edge)}
                      <span class="pair">
                        <span class="k">Edge</span>
                        <span class="v" class:pos={m.best.edge > 0} class:neg={m.best.edge < 0}>
                          {pct(m.best.edge, 1, true)}
                        </span>
                      </span>
                    {/if}
                    {#if m.stake > 0}
                      <span class="pair">
                        <span class="k">Sized</span>
                        <span class="v staked">{num(m.stake, 2)}u</span>
                      </span>
                    {/if}
                  {/if}
                </div>

                <!-- Exchanges and sportsbooks ride the same market, marked
                     apart rather than filed apart: a Kalshi contract and a
                     FanDuel line on the same total are the same bet at two
                     venues, and seeing them side by side is the point. -->
                <div class="venues">
                  {#each m.venues as v (v.venue)}
                    <span
                      class="venue"
                      class:exch={v.exchange}
                      class:best={m.best && v.venue === m.best.venue}
                      title={`${v.label}${isPrivate ? ` · ${american(v.price)}` : ''}`}
                      style={venueColor(v.venue) ? `--vc:${venueColor(v.venue)}` : ''}
                    >
                      <span class="vmark">{venueMark(v.venue)}</span>
                      {#if isPrivate}<span class="vprice">{american(v.price)}</span>{/if}
                    </span>
                  {/each}
                </div>

                {#if m.rationale}<p class="why">{m.rationale}</p>{/if}
                {#if m.note}<p class="why note">{m.note}</p>{/if}
              </div>
            {/each}
          </div>
        </section>
      {/if}

      <!-- ---- props ------------------------------------------------------
           A prop is a bet on THIS game, so it lives in this game's sheet.
           The old site gave props a board of their own, which meant the
           player market and the game it belongs to were never on screen
           together — the exact separation this rebuild exists to undo. -->
      {#if game.props.length}
        <section class="props">
          <h4>Player markets <span class="sub">{game.props.length}</span></h4>
          <div class="plist">
            {#each game.props as p, i (`${p.player}-${p.market}-${p.side}-${i}`)}
              <div class="propline">
                <span class="pwho">{p.player}</span>
                <span class="pmkt">{marketLabel(p.market)}</span>
                <span class="pcall">
                  {sideLabel(p.side)}
                  {#if isNum(p.point)}
                    {num(p.point, 1).replace('.0', '')}
                  {/if}
                </span>
                <span class="pmodel">{pct(p.p_model, 1)}</span>
                {#if isPrivate}
                  <span class="pprice2">{american(p.price)}</span>
                  {#if isNum(p.edge)}
                    <span class="pedge" class:pos={p.edge > 0} class:neg={p.edge < 0}>
                      {pct(p.edge, 1, true)}
                    </span>
                  {/if}
                {/if}
              </div>
            {/each}
          </div>
        </section>
      {/if}

      <!-- ---- what moved -------------------------------------------------
           Deliberately no claim about whether a move helped or hurt: that
           judgement is the closing-line calculation under Record, which
           measures against the actual close rather than inferring from the
           direction of travel. -->
      {#if isPrivate && game.moves.length}
        <section class="moves">
          <h4>Moved since open</h4>
          <div class="mvlist">
            {#each game.moves as mv, i (`${mv.market}-${mv.side}-${i}`)}
              <div class="mv">
                <span class="mvname">{marketLabel(mv.market)} {sideLabel(mv.side)}</span>
                <span class="mvfrom">
                  {num(mv.point_open, 1).replace('.0', '')}
                  <small>{american(mv.price_open)}</small>
                </span>
                <span class="mvarrow" aria-hidden="true">→</span>
                <span class="mvto">
                  {num(mv.point_now, 1).replace('.0', '')}
                  <small>{american(mv.price_now)}</small>
                </span>
              </div>
            {/each}
          </div>
        </section>
      {/if}

      <!-- ---- context: who is out, and the weather ---------------------- -->
      {#if game.injuries.length || (game.weather && game.weather.covered === false)}
        <section class="ctx">
          <h4>Context</h4>
          {#if game.weather && game.weather.covered === false}
            <p class="wx">
              {#if isNum(game.weather.temp_f)}
                {num(game.weather.temp_f, 0)}°F
              {/if}
              {#if isNum(game.weather.wind_mph)}
                · wind {num(game.weather.wind_mph, 0)} mph
              {/if}
              {#if isNum(game.weather.precip_pct)}
                · {pct(game.weather.precip_pct, 0)} precip
              {/if}
            </p>
          {/if}
          {#if game.injuries.length}
            <div class="outs">
              {#each game.injuries as inj, i (`${inj.player_name}-${i}`)}
                <span class="out" class:home={inj.side === 'home'}>
                  <span class="oteam">{inj.side === 'home' ? home.code : away.code}</span>
                  {inj.player_name}
                  {#if inj.position}<span class="opos">{inj.position}</span>{/if}
                </span>
              {/each}
            </div>
          {/if}
        </section>
      {/if}

      <!-- ---- your positions ------------------------------------------- -->
      {#if game.positions.length}
        <section class="mine">
          <h4>Your position</h4>
          <ul>
            {#each game.positions as p (p.bet_id)}
              <li>
                <span class="pm">{marketLabel(p.market)} {sideLabel(p.side)}</span>
                {#if p.point !== null && p.point !== undefined}
                  <span class="pp">{num(p.point, 1).replace('.0', '')}</span>
                {/if}
                {#if isPrivate}
                  <span class="pprice">{american(p.price)}</span>
                  <span class="pstake">{num(p.stake, 2)}u</span>
                {/if}
                <span class="pbook">{p.book}</span>
              </li>
            {/each}
          </ul>
        </section>
      {/if}

      <!-- ---- DFS from this game ---------------------------------------- -->
      {#if game.dfs.length}
        <section class="dfs">
          <h4>DFS plays from this game</h4>
          <div class="players">
            {#each game.dfs as p (`${p.player_name}-${p.slot ?? ''}-${p.team}`)}
              <span class="player">
                <span class="pslot">{p.slot || p.position || ''}</span>
                <span class="pname">{p.player_name}</span>
                <span class="ppts">{num(p.points, 1)}</span>
                {#if p.salary}<span class="psal">${Number(p.salary).toLocaleString()}</span>{/if}
              </span>
            {/each}
          </div>
        </section>
      {/if}

      <!-- ---- live detail ------------------------------------------------ -->
      {#if score?.event_id && score.state !== 'pre'}
        <section class="boxsec">
          <h4>Live</h4>
          <BoxScore league={game.league} eventId={score.event_id} live={isLive} />
        </section>
      {/if}
    </div>
  {/if}
</article>

<style>
  .card {
    border: 1px solid var(--v-line);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
    overflow: hidden;
    transition: border-color 140ms ease;
  }
  .card.open { border-color: var(--v-line-2); background: var(--v-lvl-1); }
  .card.live { box-shadow: inset 2px 0 0 var(--v-warn); }

  /* ---- the row ------------------------------------------------------- */
  .row {
    display: grid;
    grid-template-columns: 2.6rem minmax(0, 1fr) 6.2rem 5.4rem auto;
    align-items: center;
    gap: 0.7rem;
    width: 100%;
    padding: 0.6rem 0.8rem;
    background: transparent;
    border: 0;
    text-align: left;
    cursor: pointer;
    color: inherit;
    font: inherit;
  }
  .row:hover { background: var(--v-hover); }
  .row:focus-visible { outline: 2px solid var(--v-brand); outline-offset: -2px; }

  .lg {
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--v-ink-3);
  }

  .teams { display: grid; gap: 0.18rem; min-width: 0; }
  .side {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 0.45rem;
  }
  .name {
    font-size: 0.84rem;
    color: var(--v-ink);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .sc {
    font-family: var(--v-board);
    font-size: 1.02rem;
    font-weight: 700;
    color: var(--v-ink-2);
    font-variant-numeric: tabular-nums;
  }
  /* The leader is lit. Colour is never the only channel — the number itself
     is larger and the score is printed beside the team it belongs to. */
  .sc.win { color: var(--v-ink); }

  .state { min-width: 0; }
  .when { font-size: 0.72rem; color: var(--v-ink-3); white-space: nowrap; }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 0.3em;
    padding: 0.12rem 0.42rem;
    border-radius: 999px;
    font-family: var(--v-board);
    font-size: 0.64rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    white-space: nowrap;
  }
  .pill.live { background: var(--v-warn-tint); color: var(--v-warn); }
  .pill.final { background: var(--v-chip); color: var(--v-ink-3); }
  .dot {
    width: 5px; height: 5px; border-radius: 50%;
    background: var(--v-alert);
    animation: pulse 1.6s ease-in-out infinite;
  }
  @keyframes pulse { 50% { opacity: 0.25; } }

  .model { display: grid; gap: 0.05rem; justify-items: end; }
  .mline {
    font-family: var(--v-board);
    font-size: 0.98rem;
    font-weight: 700;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .mtotal {
    font-family: var(--v-board);
    font-size: 0.76rem;
    color: var(--v-ink-3);
    font-variant-numeric: tabular-nums;
  }
  .mnone { font-size: 0.7rem; color: var(--v-ink-3); }

  .flags { display: flex; align-items: center; gap: 0.32rem; }
  .flag {
    padding: 0.12rem 0.4rem;
    border-radius: 999px;
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    white-space: nowrap;
    background: var(--v-chip);
    color: var(--v-ink-3);
  }
  .flag.pos { background: var(--v-info-tint); color: var(--v-info); }
  .flag.staked { background: var(--v-pos-tint); color: var(--v-pos); }
  .flag.dfs { background: var(--v-brand-tint); color: var(--v-brand-dim); }
  .flag.prop { background: var(--v-warn-tint); color: var(--v-warn); }
  .chev {
    font-family: var(--v-board);
    font-size: 1rem;
    color: var(--v-ink-3);
    width: 1rem;
    text-align: center;
  }

  /* ---- the sheet ------------------------------------------------------ */
  .sheet {
    display: grid;
    gap: 1.1rem;
    padding: 0.2rem 0.8rem 1rem;
    border-top: 1px solid var(--v-line);
  }
  h4 {
    display: flex;
    align-items: baseline;
    gap: 0.6rem;
    margin: 0.7rem 0 0.5rem;
    font-size: 0.62rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--v-ink-3);
  }
  .sub { letter-spacing: 0.04em; text-transform: none; color: var(--v-ink-2); }
  .empty { font-size: 0.76rem; color: var(--v-ink-3); margin: 0; }

  .projgrid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(4.6rem, 1fr));
    gap: 0.5rem;
  }
  .stat {
    display: grid;
    gap: 0.1rem;
    padding: 0.45rem 0.55rem;
    background: var(--v-lvl-2);
    border-radius: var(--v-radius-sm);
  }
  .lab {
    font-size: 0.58rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: var(--v-ink-3);
  }
  .val {
    font-family: var(--v-board);
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }

  .sparks {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
    gap: 0.8rem;
    margin-top: 0.7rem;
  }
  figure { margin: 0; }
  figcaption {
    margin-top: 0.2rem;
    font-size: 0.64rem;
    color: var(--v-ink-3);
    display: flex;
    gap: 0.5rem;
  }
  .cut { color: var(--v-warn); }

  /* ---- the head-to-head ------------------------------------------------
     Three columns: the statistic in the middle-left, each side's value out
     to its own edge, and the advantage arrow pointing at the side that has
     it. The arrow is the second channel — the lit value alone would rest on
     colour, which nothing on this board is allowed to do. */
  .h2hgrid {
    display: grid;
    grid-template-columns: minmax(0, auto) 1fr 1fr;
    gap: 0.28rem 0.8rem;
    align-items: center;
    padding: 0.55rem 0.7rem;
    background: var(--v-lvl-2);
    border-radius: var(--v-radius-sm);
  }
  .hteam {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    font-family: var(--v-board);
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--v-ink-2);
  }
  .hteam.right { justify-content: flex-end; }
  .hlab {
    font-size: 0.58rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: var(--v-ink-3);
    white-space: nowrap;
  }
  .hval {
    display: flex;
    align-items: baseline;
    gap: 0.3em;
    font-family: var(--v-board);
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--v-ink-2);
    font-variant-numeric: tabular-nums;
  }
  .hval.right { justify-content: flex-end; }
  .hval.lead { color: var(--v-ink); font-weight: 700; }
  .harrow { font-size: 0.7em; color: var(--v-brand); }
  .h2hnote {
    margin: 0.45rem 0 0;
    font-size: 0.7rem;
    line-height: 1.5;
    color: var(--v-ink-3);
  }
  .h2hnote strong { color: var(--v-ink-2); font-weight: 600; }

  .mlist { display: grid; gap: 0.5rem; }
  .m {
    padding: 0.55rem 0.65rem;
    background: var(--v-lvl-2);
    border-radius: var(--v-radius-sm);
    display: grid;
    gap: 0.4rem;
  }
  .mhead { display: flex; align-items: baseline; gap: 0.5rem; flex-wrap: wrap; }
  .mname {
    font-family: var(--v-board);
    font-size: 1rem;
    font-weight: 700;
    color: var(--v-ink);
  }
  .mkind { font-size: 0.68rem; color: var(--v-ink-3); }
  .tier {
    padding: 0.05rem 0.34rem;
    border-radius: 4px;
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.06em;
  }
  .tA { background: var(--v-pos-tint); color: var(--v-pos); }
  .tB { background: var(--v-info-tint); color: var(--v-info); }
  .tC { background: var(--v-thin-tint); color: var(--v-thin); }
  /* The monitor's flag, on the market it is about. Amber because it is a
     question; the exclusion candidate — a confirmed 30-day loser — is the
     one that gets the loss colour. */
  .health {
    padding: 0.05rem 0.38rem;
    border-radius: 999px;
    font-size: 0.64rem;
    font-weight: 600;
    background: var(--v-warn-tint);
    color: var(--v-warn);
  }
  .health.bad { background: var(--v-neg-tint); color: var(--v-neg); }

  .mnums { display: flex; flex-wrap: wrap; gap: 0.9rem; }
  .pair { display: grid; gap: 0.02rem; }
  .k {
    font-size: 0.56rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: var(--v-ink-3);
  }
  .v {
    font-family: var(--v-board);
    font-size: 0.92rem;
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  .v.pos { color: var(--v-pos); }
  .v.neg { color: var(--v-neg); }
  .v.staked { color: var(--v-pos); }

  .venues { display: flex; flex-wrap: wrap; gap: 0.28rem; }
  .venue {
    display: inline-flex;
    align-items: center;
    gap: 0.3em;
    padding: 0.1rem 0.34rem 0.1rem 0.1rem;
    border-radius: 6px;
    background: var(--v-chip);
    box-shadow: inset 0 0 0 1px var(--v-line);
    font-size: 0.7rem;
    color: var(--v-ink-2);
  }
  .venue.best {
    box-shadow: inset 0 0 0 1px rgba(61, 218, 208, 0.42);
    background: var(--v-brand-tint);
    color: var(--v-ink);
  }
  .vmark {
    display: inline-grid;
    place-items: center;
    width: 1.15rem;
    height: 1.15rem;
    border-radius: 4px;
    font-family: var(--v-board);
    font-size: 0.58rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    color: var(--vc, var(--v-ink-3));
    background: rgba(255, 255, 255, 0.05);
  }
  /* An exchange contract is not a sportsbook line; the dashed edge says so
     without spending a colour on it. */
  .venue.exch { border-radius: 6px; box-shadow: inset 0 0 0 1px var(--v-line-2); }
  .venue.exch .vmark { box-shadow: inset 0 0 0 1px var(--vc, var(--v-line-2)); }
  .vprice { font-variant-numeric: tabular-nums; font-family: var(--v-board); font-weight: 600; }

  .why {
    margin: 0;
    font-size: 0.72rem;
    line-height: 1.5;
    color: var(--v-ink-3);
  }
  .why.note { color: var(--v-warn); }

  /* ---- props, moves, context ------------------------------------------ */
  .plist { display: grid; gap: 0.2rem; }
  /* `.propline`, not `.prop`: the row flag on the collapsed card is
     `.flag.prop`, and a bare `.prop` rule here matched it too — turning a
     small pill into a 129px grid. Svelte scopes styles per COMPONENT, not per
     block, so two unrelated things in one file can still collide. */
  .propline {
    display: grid;
    grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) auto auto auto auto;
    align-items: baseline;
    gap: 0.6rem;
    padding: 0.3rem 0.55rem;
    background: var(--v-lvl-2);
    border-radius: var(--v-radius-sm);
    font-size: 0.78rem;
  }
  .pwho {
    color: var(--v-ink);
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .pmkt {
    color: var(--v-ink-3);
    font-size: 0.7rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .pcall, .pmodel, .pprice2, .pedge {
    font-family: var(--v-board);
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .pmodel { color: var(--v-ink-2); }
  .pedge.pos { color: var(--v-pos); }
  .pedge.neg { color: var(--v-neg); }

  .mvlist { display: grid; gap: 0.2rem; }
  .mv {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.5rem;
    font-size: 0.78rem;
    color: var(--v-ink-2);
  }
  .mvname { min-width: 8rem; color: var(--v-ink); }
  .mvfrom, .mvto {
    font-family: var(--v-board);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--v-ink);
  }
  .mvfrom { color: var(--v-ink-3); }
  .mvfrom small, .mvto small { font-size: 0.72em; color: var(--v-ink-3); margin-left: 0.25em; }
  .mvarrow { color: var(--v-ink-3); }

  .wx { margin: 0 0 0.4rem; font-size: 0.76rem; color: var(--v-ink-2); }
  .outs { display: flex; flex-wrap: wrap; gap: 0.3rem; }
  .out {
    display: inline-flex;
    align-items: baseline;
    gap: 0.35em;
    padding: 0.18rem 0.45rem;
    border-radius: var(--v-radius-sm);
    background: var(--v-neg-tint);
    font-size: 0.74rem;
    color: var(--v-ink-2);
  }
  .oteam {
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    color: var(--v-neg);
  }
  .opos { font-size: 0.64rem; color: var(--v-ink-3); }

  .mine ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 0.3rem; }
  .mine li {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.6rem;
    padding: 0.35rem 0.55rem;
    background: var(--v-info-tint);
    border-radius: var(--v-radius-sm);
    font-size: 0.78rem;
  }
  .pm { color: var(--v-ink); font-weight: 600; }
  .pp, .pprice, .pstake {
    font-family: var(--v-board);
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  .pbook { color: var(--v-ink-3); font-size: 0.7rem; margin-left: auto; }

  .players { display: flex; flex-wrap: wrap; gap: 0.35rem; }
  .player {
    display: inline-flex;
    align-items: baseline;
    gap: 0.4em;
    padding: 0.24rem 0.5rem;
    background: var(--v-lvl-2);
    border-radius: var(--v-radius-sm);
    font-size: 0.76rem;
  }
  .pslot {
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    color: var(--v-ink-3);
  }
  .pname { color: var(--v-ink); }
  .ppts {
    font-family: var(--v-board);
    font-weight: 700;
    color: var(--v-brand);
    font-variant-numeric: tabular-nums;
  }
  .psal { font-size: 0.68rem; color: var(--v-ink-3); font-variant-numeric: tabular-nums; }

  /* ---- phone ---------------------------------------------------------
     The row drops to two columns: the matchup with its scores, and a right
     column carrying the state and the model's line. The flags wrap under.
     Nothing is hidden — a board that hides the number on a phone is a board
     you stop using on a phone. */
  @media (max-width: 720px) {
    .row {
      grid-template-columns: minmax(0, 1fr) auto;
      grid-template-areas:
        "lg     state"
        "teams  model"
        "flags  flags";
      gap: 0.35rem 0.6rem;
    }
    .lg { grid-area: lg; }
    .teams { grid-area: teams; }
    .state { grid-area: state; justify-self: end; }
    .model { grid-area: model; }
    .flags { grid-area: flags; flex-wrap: wrap; }
    .chev { margin-left: auto; }
  }
</style>
