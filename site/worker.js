// Velocity site Worker: static assets + the DuckDB-WASM detour.
//
// Evidence ships two DuckDB-WASM binaries (~33 and ~38 MiB) that exceed
// Cloudflare's 25 MiB per-asset cap, so the deploy step parks them in an R2
// bucket (keyed by basename — the names are content-hashed, so immutable)
// and strips them from the asset upload. This handler serves those two
// paths from R2 and hands everything else to the static assets binding.

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.endsWith(".wasm")) {
      const key = url.pathname.split("/").pop();
      const object = await env.WASM.get(key);
      if (object === null) {
        return new Response("wasm asset not found", { status: 404 });
      }
      return new Response(object.body, {
        headers: {
          "content-type": "application/wasm",
          "cache-control": "public, max-age=31536000, immutable",
        },
      });
    }
    return env.ASSETS.fetch(request);
  },
};
