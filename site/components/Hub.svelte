<script>
  // The hub's entry point.
  //
  // Evidence auto-imports components from this directory by filename, so the
  // single name the page needs lives here and the app itself lives in ./hub/,
  // where the pieces import each other by relative path and never depend on
  // that resolution.
  import Shell from './hub/Shell.svelte';
</script>

<Shell {...$$props} />
