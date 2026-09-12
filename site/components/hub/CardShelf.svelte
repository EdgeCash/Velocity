<script>
  // The rendered graphics: the pre-game sheet, the sim check, the record card.
  //
  // These are made to be POSTED — that is the only reason they exist as PNGs
  // rather than as the numbers already on this page — so the shelf is built
  // around the two things you do with one: look at it full size, and take the
  // caption that goes with it. Everything else is chrome.
  //
  // The images are hot-linked from the site's own static dir, refreshed per
  // run. A card whose file did not make it into the build is dropped rather
  // than left as a broken frame, which is what `broken` tracks.
  export let cards = [];
  /** Shown when the shelf is empty; omit to render nothing at all. */
  export let emptyNote = '';

  const KIND_LABEL = {
    sheet: 'Pre-game sheet',
    simcheck: 'Sim check',
    recordcard: 'Record',
    social: 'Social',
    deepdive: 'Deep dive',
    grid: 'Broadcast grid',
  };

  let broken = {};
  const fail = (file) => { broken = { ...broken, [file]: true }; };

  let copied = '';
  let timer;
  async function copy(card) {
    try {
      await navigator.clipboard.writeText(String(card.caption ?? ''));
      copied = card.file;
      clearTimeout(timer);
      timer = setTimeout(() => { copied = ''; }, 1800);
    } catch {
      // Clipboard access is refused in some embeddings and over plain http.
      // The caption is printed right there, so failing quietly costs nothing
      // a reader cannot work around by selecting it.
      copied = '';
    }
  }

  $: shown = (cards ?? []).filter((c) => c.file && !broken[c.file]);
</script>

{#if shown.length}
  <div class="shelf">
    {#each shown as card (card.file)}
      <figure>
        <a href={`/cards/${card.file}`} target="_blank" rel="noreferrer">
          <img
            src={`/cards/${card.file}`}
            alt={`${KIND_LABEL[card.kind] ?? card.kind} — ${card.away} at ${card.home}`}
            loading="lazy"
            on:error={() => fail(card.file)}
          />
        </a>
        <figcaption>
          <span class="kind">{KIND_LABEL[card.kind] ?? card.kind}</span>
          {#if card.caption}
            <button class="copy" class:done={copied === card.file} on:click={() => copy(card)}>
              {copied === card.file ? 'Copied' : 'Copy caption'}
            </button>
          {/if}
        </figcaption>
        {#if card.caption}
          <p class="caption">{card.caption}</p>
        {/if}
      </figure>
    {/each}
  </div>
{:else if emptyNote}
  <p class="empty">{emptyNote}</p>
{/if}

<style>
  .shelf {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
    gap: 0.7rem;
  }
  figure {
    margin: 0;
    display: grid;
    gap: 0.3rem;
    padding: 0.5rem;
    background: var(--v-lvl-2);
    border-radius: var(--v-radius-sm);
    align-content: start;
  }
  a {
    display: block;
    border-radius: 6px;
    overflow: hidden;
    line-height: 0;
    background: var(--v-bg);
  }
  a:focus-visible { outline: 2px solid var(--v-brand); outline-offset: 2px; }
  img {
    width: 100%;
    height: auto;
    display: block;
    transition: opacity 130ms ease;
  }
  a:hover img { opacity: 0.88; }

  figcaption {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .kind {
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--v-ink-3);
  }
  .copy {
    border: 0;
    background: transparent;
    padding: 0;
    color: var(--v-brand);
    font-size: 0.68rem;
    cursor: pointer;
    white-space: nowrap;
  }
  .copy:hover { text-decoration: underline; }
  .copy.done { color: var(--v-pos); }

  /* The caption is the post. It is long, so it is clamped — but selectable,
     so the copy button failing is never the end of the road. */
  .caption {
    margin: 0;
    font-size: 0.68rem;
    line-height: 1.5;
    color: var(--v-ink-3);
    white-space: pre-line;
    max-height: 5.4em;
    overflow: auto;
    scrollbar-width: thin;
  }

  .empty { margin: 0; font-size: 0.76rem; color: var(--v-ink-3); }
</style>
