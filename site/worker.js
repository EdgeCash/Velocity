// Velocity site Worker: static assets, the DuckDB-WASM detour, and the
// live-scores proxy.
//
// WASM: Evidence ships two DuckDB-WASM binaries (~33 and ~38 MiB) that
// exceed Cloudflare's 25 MiB per-asset cap, so the deploy step parks them in
// an R2 bucket (keyed by basename — the names are content-hashed, so
// immutable) and strips them from the asset upload. This handler serves
// those paths from R2.
//
// Scores: /api/scores fans out to ESPN's public scoreboard JSON for the five
// leagues, trims each event to what the ticker renders, and edge-caches the
// result for ~45s so a page full of viewers costs ESPN one request.

const SCOREBOARDS = {
  NFL: "football/nfl",
  CFB: "football/college-football",
  MLB: "baseball/mlb",
  WNBA: "basketball/wnba",
  CBB: "basketball/mens-college-basketball",
};

async function fetchScores() {
  const games = [];
  await Promise.all(
    Object.entries(SCOREBOARDS).map(async ([lg, path]) => {
      try {
        const res = await fetch(
          `https://site.api.espn.com/apis/site/v2/sports/${path}/scoreboard`,
          { headers: { accept: "application/json" } },
        );
        if (!res.ok) return;
        const data = await res.json();
        for (const event of data.events ?? []) {
          const comp = event.competitions?.[0];
          if (!comp) continue;
          const away = comp.competitors?.find((c) => c.homeAway === "away");
          const home = comp.competitors?.find((c) => c.homeAway === "home");
          if (!away?.team || !home?.team) continue;
          games.push({
            lg,
            away: away.team.abbreviation ?? away.team.shortDisplayName,
            home: home.team.abbreviation ?? home.team.shortDisplayName,
            as: Number(away.score ?? 0),
            hs: Number(home.score ?? 0),
            state: event.status?.type?.state ?? "pre",
            detail: event.status?.type?.shortDetail ?? "",
            start: event.date ?? "",
          });
        }
      } catch {
        // one dark league never blanks the ticker
      }
    }),
  );
  // Live games lead the crawl, then upcoming, then finals.
  const order = { in: 0, pre: 1, post: 2 };
  games.sort((a, b) => (order[a.state] ?? 3) - (order[b.state] ?? 3));
  return games;
}

async function scoresResponse(url, ctx) {
  const cache = caches.default;
  const key = new Request(new URL("/api/scores", url.origin));
  const cached = await cache.match(key);
  if (cached) return cached;
  const games = await fetchScores();
  const response = new Response(
    JSON.stringify({ updated: new Date().toISOString(), games }),
    {
      headers: {
        "content-type": "application/json",
        "cache-control": "public, max-age=30, s-maxage=45",
      },
    },
  );
  ctx.waitUntil(cache.put(key, response.clone()));
  return response;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/api/scores") {
      return scoresResponse(url, ctx);
    }
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
