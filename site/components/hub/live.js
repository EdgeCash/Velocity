// Live scores and box scores, as one store the whole hub reads.
//
// ESPN's public scoreboard JSON sends `access-control-allow-origin: *` but its
// Akamai edge 403s datacenter IPs — Cloudflare Worker egress included, and the
// build container's too. So the BROWSER fetches ESPN directly (viewer IPs are
// served fine) and the Worker's `/api/scores` proxy is only a fallback for a
// viewer whose own network blocks it. That is the same arrangement the old
// marquee ticker used; what is new here is that the hub needs three things the
// ticker never did:
//
//   * **the right dates.** The scoreboard defaults to today in US/Eastern. A
//     Friday-night board carrying Sunday's NFL slate would come back empty
//     from that default, so the dates are driven by the games we actually
//     hold rather than by ESPN's idea of now.
//   * **the whole college board.** College football and men's college
//     basketball default to the top 25. `groups=80` / `groups=50` is what
//     opens them to every FBS / D-I game, which is most of our board.
//   * **a join back to our own game_id.** A score is only useful here if it
//     can be attached to the model's projection and to an open position, and
//     the two sides name teams differently (we carry "Pittsburgh Steelers",
//     ESPN also offers "PIT", "Steelers" and "Pittsburgh"). `indexGames`
//     below registers every spelling we can derive so the match does not rest
//     on one of them being right.
//
// **Nothing here imports Svelte.** The store that drives the UI lives next
// door in `liveStore.js`, and the split is not tidiness: it is what lets
// `site/tests/hub.test.mjs` run under bare `node --test` with no `npm ci` and
// no node_modules at all. A single `svelte/store` import in this file takes
// the whole suite out of CI, which is exactly how it first shipped.

/** Our league codes → ESPN's sport/league path segment. */
const ESPN_PATH = {
  nfl: 'football/nfl',
  ncaaf: 'football/college-football',
  mlb: 'baseball/mlb',
  wnba: 'basketball/wnba',
  ncaab: 'basketball/mens-college-basketball',
  nhl: 'hockey/nhl',
};

// The college scoreboards answer with the top 25 unless a group is named.
// 80 is FBS, 50 is D-I men's basketball. `limit` is raised with them because
// a full Saturday clears the default page size.
const LEAGUE_QUERY = {
  ncaaf: 'groups=80&limit=300',
  ncaab: 'groups=50&limit=300',
};

/** Teams are matched on a squashed key, so punctuation and case never decide it. */
export function normTeam(value) {
  return String(value ?? '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '');
}

/** A Date (or ISO string) → ESPN's `YYYYMMDD`, in US/Eastern.
 *
 * ESPN's sports day is an Eastern-time day: a 10pm Pacific tip is still
 * "tonight" to the scoreboard. Asking in the viewer's own zone would fetch the
 * wrong day for anyone west of Eastern after 9pm, which is exactly when a
 * bettor is watching.
 */
export function espnDate(value) {
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  // en-CA renders as YYYY-MM-DD, which is the only reason to pick it here.
  const parts = d.toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
  return parts.replace(/-/g, '');
}

/** The (league → dates) fan-out a set of games implies.
 *
 * A game's kickoff is banked in UTC; its Eastern date is the one the
 * scoreboard files it under. Today is always included per league so a live
 * game that was never on our board (a team we do not price, a postponement
 * moved forward) still reaches the ticker.
 */
export function scoreboardPlan(games, now = new Date()) {
  const plan = {};
  const today = espnDate(now);
  for (const game of games ?? []) {
    const league = String(game?.league ?? '').toLowerCase();
    if (!ESPN_PATH[league]) continue;
    const date = game?.kickoff ? espnDate(game.kickoff) : null;
    const dates = (plan[league] ??= new Set());
    if (today) dates.add(today);
    if (date) dates.add(date);
  }
  return Object.fromEntries(
    Object.entries(plan).map(([lg, set]) => [lg, [...set].sort()]),
  );
}

/** Every spelling of a game we can look it up by.
 *
 * Keyed `league|away|home` on each of: our own team strings, and the team
 * codes from the identity table. ESPN is then tried against all of them —
 * displayName, abbreviation, shortDisplayName and location — and the first
 * hit wins. The alternative, picking one spelling and hoping, silently drops
 * whole leagues: our college names carry the mascot ("Miami Hurricanes")
 * where ESPN's `location` does not.
 */
export function indexGames(games, teamIndex = {}) {
  const out = {};
  for (const game of games ?? []) {
    const league = String(game?.league ?? '').toLowerCase();
    if (!league || league === '__none__') continue;
    const home = String(game?.home_team ?? '');
    const away = String(game?.away_team ?? '');
    if (!home || !away) continue;
    const codeOf = (team) => {
      const hit = teamIndex[`${league}|${team}`] || teamIndex[team];
      return hit?.code ? String(hit.code) : '';
    };
    const spellings = [
      [away, home],
      [codeOf(away), codeOf(home)],
    ];
    for (const [a, h] of spellings) {
      if (!a || !h) continue;
      out[`${league}|${normTeam(a)}|${normTeam(h)}`] = game.game_id;
    }
  }
  return out;
}

/** One ESPN event, trimmed to what the hub renders. */
function trimEvent(league, event) {
  const comp = event?.competitions?.[0];
  if (!comp) return null;
  const away = comp.competitors?.find((c) => c.homeAway === 'away');
  const home = comp.competitors?.find((c) => c.homeAway === 'home');
  if (!away?.team || !home?.team) return null;
  const name = (t) => ({
    abbr: t.abbreviation ?? t.shortDisplayName ?? '',
    display: t.displayName ?? '',
    short: t.shortDisplayName ?? '',
    location: t.location ?? '',
    logo: t.logo ?? '',
  });
  return {
    league,
    event_id: String(event.id ?? ''),
    away: name(away.team),
    home: name(home.team),
    // `score` arrives as a string; a pre-game one is "0", which is a real 0
    // and not missing, so it is coerced rather than defaulted.
    away_score: Number(away.score ?? 0),
    home_score: Number(home.score ?? 0),
    // 'pre' | 'in' | 'post'
    state: event.status?.type?.state ?? 'pre',
    detail: event.status?.type?.shortDetail ?? '',
    start: event.date ?? '',
    // Only set once we match it to one of ours; a game we do not price still
    // rides the ticker with a null game_id.
    game_id: null,
  };
}

/** Attach our game_id to an ESPN event, trying every spelling both sides offer. */
export function attachGameId(trimmed, index) {
  const lg = trimmed.league;
  const fields = ['display', 'abbr', 'short', 'location'];
  for (const field of fields) {
    const a = normTeam(trimmed.away[field]);
    const h = normTeam(trimmed.home[field]);
    if (!a || !h) continue;
    const hit = index[`${lg}|${a}|${h}`];
    if (hit) return hit;
  }
  return null;
}

export async function fetchJson(url, signal) {
  const res = await fetch(url, { headers: { accept: 'application/json' }, signal });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

/** Every scoreboard the plan asks for, flattened and joined to our games. */
export async function fetchScoreboards(plan, index, signal) {
  const calls = [];
  for (const [league, dates] of Object.entries(plan ?? {})) {
    const path = ESPN_PATH[league];
    if (!path) continue;
    for (const date of dates) {
      const extra = LEAGUE_QUERY[league] ? `&${LEAGUE_QUERY[league]}` : '';
      calls.push([
        league,
        `https://site.api.espn.com/apis/site/v2/sports/${path}/scoreboard?dates=${date}${extra}`,
      ]);
    }
  }
  const settled = await Promise.allSettled(
    calls.map(async ([league, url]) => {
      const data = await fetchJson(url, signal);
      return (data?.events ?? [])
        .map((event) => trimEvent(league, event))
        .filter(Boolean);
    }),
  );
  // One dark league never blanks the board; a rejected call just contributes
  // nothing. `ok` tells the caller whether ANY call landed, which is what
  // separates "no games today" from "ESPN unreachable from this network".
  const games = [];
  let ok = false;
  const seen = new Set();
  for (const result of settled) {
    if (result.status !== 'fulfilled') continue;
    ok = true;
    for (const game of result.value) {
      // The same event can arrive from two dates (a late game crossing
      // midnight Eastern is filed under both); keep the first.
      if (game.event_id && seen.has(game.event_id)) continue;
      if (game.event_id) seen.add(game.event_id);
      game.game_id = attachGameId(game, index);
      games.push(game);
    }
  }
  return { games, ok };
}

/* ------------------------------------------------------------------ *
 * The box score.
 *
 * ESPN's summary endpoint returns per-team stat blocks that each carry their
 * OWN `labels` array alongside each athlete's `stats` array. This renderer
 * reads those labels rather than naming columns itself, which is deliberate
 * on two counts: it works for every sport without a per-sport table, and it
 * survives ESPN renaming or reordering a column — the one kind of change that
 * would otherwise silently mislabel a row of numbers.
 *
 * Anything that does not match the shape yields an empty list, and the panel
 * renders its empty state rather than a broken table.
 * ------------------------------------------------------------------ */

/** The summary endpoint's player blocks → `[{team, heading, columns, rows}]`. */
export function parseBoxScore(summary) {
  const blocks = [];
  for (const group of summary?.boxscore?.players ?? []) {
    const team = group?.team?.abbreviation
      ?? group?.team?.shortDisplayName
      ?? group?.team?.displayName
      ?? '';
    for (const stat of group?.statistics ?? []) {
      const columns = (stat?.labels ?? []).map((l) => String(l));
      const athletes = stat?.athletes ?? [];
      if (!columns.length || !athletes.length) continue;
      const rows = [];
      for (const entry of athletes) {
        const who = entry?.athlete;
        if (!who) continue;
        rows.push({
          id: String(who.id ?? who.displayName ?? rows.length),
          name: String(who.shortName ?? who.displayName ?? ''),
          position: String(entry?.position?.abbreviation ?? who?.position?.abbreviation ?? ''),
          // Trimmed to the labels actually declared: a stats array longer than
          // its own header is ESPN's bug, not a column we should invent a name
          // for.
          stats: columns.map((_, i) => String(entry?.stats?.[i] ?? '')),
        });
      }
      if (!rows.length) continue;
      blocks.push({
        team,
        heading: String(stat?.text ?? stat?.name ?? ''),
        columns,
        rows,
      });
    }
  }
  return blocks;
}

/** Scoring plays, when the sport has them — the other half of "what happened". */
export function parseScoringPlays(summary) {
  const out = [];
  for (const play of summary?.scoringPlays ?? []) {
    out.push({
      id: String(play?.id ?? out.length),
      team: String(play?.team?.abbreviation ?? ''),
      text: String(play?.text ?? ''),
      clock: String(play?.clock?.displayValue ?? ''),
      period: Number(play?.period?.number ?? 0),
      away_score: Number(play?.awayScore ?? 0),
      home_score: Number(play?.homeScore ?? 0),
    });
  }
  return out;
}

/** One game's live detail. Returns nulls rather than throwing on a bad shape. */
export async function fetchSummary(league, eventId, signal) {
  const path = ESPN_PATH[String(league ?? '').toLowerCase()];
  if (!path || !eventId) return { box: [], plays: [], ok: false };
  try {
    const data = await fetchJson(
      `https://site.api.espn.com/apis/site/v2/sports/${path}/summary?event=${eventId}`,
      signal,
    );
    return {
      box: parseBoxScore(data),
      plays: parseScoringPlays(data),
      ok: true,
    };
  } catch {
    return { box: [], plays: [], ok: false };
  }
}
