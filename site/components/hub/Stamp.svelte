<script>
  // When the board you are looking at was last updated.
  //
  // This is a static site: the parquet under it is whatever the last
  // live-slate run published, and nothing on the page moves except the live
  // scores. So the single most misleading thing the surface can do is look
  // alive while showing yesterday's slate — a board of prices with no age on
  // it reads as current, and a stale price is the one wrong number that
  // actually costs money.
  //
  // Hence a RELATIVE age rather than a timestamp. "Sep 12, 23:51 UTC" needs
  // arithmetic and a timezone conversion before it means anything; "18h ago"
  // is the answer to the question you were really asking. The absolute time,
  // in UTC and in the viewer's own zone, is one hover away.
  import { onDestroy, onMount } from 'svelte';
  import { ageLabel, ageTone, localLabel, stampLabel, stampTime } from '../format.js';

  /** The slate capture stamp — when the DATA is from. */
  export let stamp = '';
  /** When build_site_data.py ran — when the PAGE was made. */
  export let builtAt = '';

  $: at = stampTime(stamp);
  $: built = stampTime(builtAt);

  // Read on a timer, not once at render. A hub is a dashboard: the tab gets
  // left open, and a page that decided it was "2m ago" at breakfast would
  // still be saying so at dinner — the exact staleness the chip exists to
  // catch, dressed up as freshness. Thirty seconds keeps the minutes honest
  // without being a render loop.
  let now = Date.now();
  let timer;
  onMount(() => {
    now = Date.now();
    timer = setInterval(() => { now = Date.now(); }, 30_000);
  });
  onDestroy(() => clearInterval(timer));

  $: age = at === null ? NaN : now - at;
  $: tone = ageTone(age);
  $: label = ageLabel(age);

  // The hover carries what the chip compresses away: the absolute instant both
  // ways, and the build time when it differs from the capture by enough to
  // matter (a rebuild over banked artifacts can publish an hour after the
  // slate it is showing, and then the two numbers genuinely disagree).
  $: detail = (() => {
    const parts = [];
    if (at !== null) {
      parts.push(`Slate captured ${stampLabel(stamp)}`);
      const local = localLabel(stamp);
      if (local) parts.push(`local ${local}`);
    } else {
      parts.push('No slate stamp in this build');
    }
    if (built !== null && (at === null || Math.abs(built - at) > 5 * 60_000)) {
      parts.push(`site built ${stampLabel(builtAt)}`);
    }
    return parts.join(' · ');
  })();
</script>

<span
  class="upd"
  class:aging={tone === 'aging'}
  class:stale={tone === 'stale'}
  title={detail}
>
  <span class="dot" aria-hidden="true"></span>
  <span class="lab">Updated</span>
  <span class="age">{label}</span>
</span>

<style>
  .upd {
    display: inline-flex;
    align-items: baseline;
    gap: 0.35em;
    flex: 0 0 auto;
    padding: 0.16rem 0.5rem;
    border-radius: 999px;
    background: var(--v-chip);
    box-shadow: inset 0 0 0 1px var(--v-line);
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--v-ink-3);
    white-space: nowrap;
    cursor: help;
  }
  .age {
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.06em;
  }

  /* The dot is the fast read and the words are the slow one. Colour alone
     never carries it — `aging` and `stale` also change the ring and the text,
     so the chip still says something under deuteranopia. */
  .dot {
    align-self: center;
    width: 0.42em;
    height: 0.42em;
    border-radius: 50%;
    background: var(--v-brand);
    box-shadow: 0 0 0 3px rgba(47, 109, 50, 0.14);
  }
  .aging {
    color: var(--v-warn);
    box-shadow: inset 0 0 0 1px rgba(133, 87, 0, 0.35);
  }
  .aging .dot { background: var(--v-warn); box-shadow: 0 0 0 3px var(--v-warn-tint); }
  .aging .age { color: var(--v-warn); }
  .stale {
    color: var(--v-alert);
    box-shadow: inset 0 0 0 1px rgba(197, 34, 31, 0.42);
  }
  .stale .dot { background: var(--v-alert); box-shadow: 0 0 0 3px rgba(197, 34, 31, 0.18); }
  .stale .age { color: var(--v-alert); }

  /* On a phone the topbar is already carrying the wordmark, the tier pill and
     the live ticker. "Updated" is the word you can lose — the dot and the
     number still say everything the chip is for. */
  @media (max-width: 640px) {
    .upd { letter-spacing: 0.06em; padding: 0.16rem 0.4rem; }
    .lab { display: none; }
  }
</style>
