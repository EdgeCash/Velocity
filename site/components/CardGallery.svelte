<script>
  // Grid of rendered card PNGs with per-card download + caption reveal.
  // `cards` is a query result with file / away / home / league / caption.
  export let cards = [];
  export let empty = 'No cards rendered yet.';

  async function copyCaption(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // clipboard unavailable — the caption is still visible to select
    }
  }
</script>

{#if cards.length}
  <div class="cg">
    {#each cards as c}
      <figure class="cg-card">
        <a href={`/cards/${c.file}`} target="_blank" rel="noopener">
          <img src={`/cards/${c.file}`} alt={c.away ? `${c.away} @ ${c.home}` : c.league.toUpperCase()} loading="lazy" />
        </a>
        <figcaption>
          <span class="cg-label">
            <span class="cg-lg">{c.league.toUpperCase()}</span>
            {c.away ? `${c.away} @ ${c.home}` : 'Season record'}
          </span>
          <span class="cg-actions">
            {#if c.caption}
              <button class="cg-btn" on:click={() => copyCaption(c.caption)} title="Copy the post caption">caption</button>
            {/if}
            <a class="cg-btn" href={`/cards/${c.file}`} download>save</a>
          </span>
        </figcaption>
        {#if c.caption}
          <details class="cg-caption">
            <summary>post text</summary>
            <pre>{c.caption}</pre>
          </details>
        {/if}
      </figure>
    {/each}
  </div>
{:else}
  <p class="cg-empty">{empty}</p>
{/if}

<style>
  .cg {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(340px, 100%), 1fr));
    gap: 1rem;
    margin: 0.5rem 0 1.5rem;
  }
  .cg-card {
    margin: 0;
    background: #0d141c;
    border: 1px solid #1d2733;
    border-radius: 10px;
    overflow: hidden;
    transition: transform 0.15s ease, border-color 0.15s ease;
  }
  .cg-card:hover {
    transform: translateY(-2px);
    border-color: #3ddad0;
  }
  .cg-card img {
    display: block;
    width: 100%;
    height: auto;
  }
  figcaption {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.5rem 0.7rem;
    font-size: 0.78rem;
    color: #b7c2cc;
  }
  .cg-lg {
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: #5b6b7a;
    margin-right: 0.4em;
  }
  .cg-actions {
    display: inline-flex;
    gap: 0.4rem;
  }
  .cg-btn {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: #3ddad0;
    background: rgba(61, 218, 208, 0.08);
    border: 1px solid rgba(61, 218, 208, 0.25);
    border-radius: 6px;
    padding: 0.15rem 0.55rem;
    cursor: pointer;
    text-decoration: none;
  }
  .cg-btn:hover {
    background: rgba(61, 218, 208, 0.16);
  }
  .cg-caption {
    border-top: 1px solid #1d2733;
    padding: 0.35rem 0.7rem 0.6rem;
    font-size: 0.72rem;
    color: #8fa0af;
  }
  .cg-caption summary {
    cursor: pointer;
    color: #5b6b7a;
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .cg-caption pre {
    white-space: pre-wrap;
    user-select: text;
    font-family: inherit;
    margin: 0.4rem 0 0;
  }
  .cg-empty {
    color: #5b6b7a;
    font-size: 0.85rem;
  }
</style>
