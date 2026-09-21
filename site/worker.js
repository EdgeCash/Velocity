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
//
// Board: /board renders the private mobile board from the export CSVs parked
// in R2 by the slate run (docs/PHASE13_STAGE2_CLOUDFLARE.md). Read-only by
// design — GET and HEAD only, no dispatch, no secret. Runs are started from
// the iOS Shortcut, which is the single control plane.

import { BOARD_FILES, boardModel, renderBoard } from "./board.js";

const SCOREBOARDS = {
  NFL: "football/nfl",
  CFB: "football/college-football",
  MLB: "baseball/mlb",
  WNBA: "basketball/wnba",
  CBB: "basketball/mens-college-basketball",
};

async function fetchScores(statuses) {
  const games = [];
  await Promise.all(
    Object.entries(SCOREBOARDS).map(async ([lg, path]) => {
      try {
        // ESPN's edge 403s empty and browser-spoof user agents from
        // datacenter IPs but passes honest tool UAs — send one.
        const res = await fetch(
          `https://site.api.espn.com/apis/site/v2/sports/${path}/scoreboard`,
          {
            headers: {
              accept: "application/json",
              "user-agent": "velocity-edge/1.0 (scores ticker)",
            },
          },
        );
        if (statuses) statuses[lg] = res.status;
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
      } catch (err) {
        // one dark league never blanks the ticker
        if (statuses) statuses[lg] = `error: ${err?.message ?? err}`;
      }
    }),
  );
  // Live games lead the crawl, then upcoming, then finals.
  const order = { in: 0, pre: 1, post: 2 };
  games.sort((a, b) => (order[a.state] ?? 3) - (order[b.state] ?? 3));
  return games;
}

async function scoresResponse(url, ctx) {
  const debug = url.searchParams.has("debug");
  const cache = caches.default;
  const key = new Request(new URL("/api/scores", url.origin));
  if (!debug) {
    const cached = await cache.match(key);
    if (cached) return cached;
  }
  const statuses = debug ? {} : null;
  const games = await fetchScores(statuses);
  const body = { updated: new Date().toISOString(), games };
  if (debug) body.statuses = statuses;
  const response = new Response(JSON.stringify(body), {
    headers: {
      "content-type": "application/json",
      "cache-control": "public, max-age=30, s-maxage=45",
    },
  });
  if (!debug) ctx.waitUntil(cache.put(key, response.clone()));
  return response;
}

// The board's CSVs live under one R2 prefix, keyed by their export filename
// with no stamp — the same stable-name discipline the CSVs already follow, so
// the newest write wins and this reads a fixed key.
const BOARD_PREFIX = "board/";

async function boardResponse(request, env) {
  // Read-only is a property to enforce, not to document. Anything that could
  // change state is refused here rather than merely unimplemented.
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("the board is read-only", {
      status: 405,
      headers: { allow: "GET, HEAD" },
    });
  }
  const bucket = env.BOARD ?? env.WASM;
  const files = {};
  await Promise.all(
    BOARD_FILES.map(async (name) => {
      try {
        const object = await bucket.get(BOARD_PREFIX + name);
        if (object) files[name] = await object.text();
      } catch {
        // A missing or unreadable file is an absent section, never a 500:
        // half a board is worth more than an error page before kickoff.
      }
    }),
  );
  const html = renderBoard(boardModel(files));
  return new Response(request.method === "HEAD" ? null : html, {
    headers: {
      "content-type": "text/html; charset=utf-8",
      // Never cached. The page states the data's own age, and an edge copy
      // would let a stale board answer a deliberate reload — the one failure
      // this design must not have.
      "cache-control": "no-store",
      "referrer-policy": "no-referrer",
      "x-robots-tag": "noindex, nofollow",
    },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/api/scores") {
      return scoresResponse(url, ctx);
    }
    if (url.pathname === "/board" || url.pathname === "/board/") {
      return boardResponse(request, env);
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
