# Data access — what's reachable, and how to get EPA data flowing

**Status:** the model runs end-to-end on real NFL data *except* play-by-play
(EPA), which is not reachable from this managed sandbox. This doc records the
accurate root cause and the real ways to unblock it.

## Probe results

| Data | Host | Result |
|---|---|---|
| Schedules + final scores + closing lines | `raw.githubusercontent.com` | ✅ `200` — reachable |
| Play-by-play (EPA) | `github.com/nflverse/nflverse-data/releases/download/…` | ❌ `403` |
| Weekly rosters | `github.com/nflverse/nflverse-data/releases/download/…` | ❌ `403` |

## Root cause — it is *not* the network access level

The release-asset CDN hosts (`github.com`, `objects.githubusercontent.com`,
`release-assets.githubusercontent.com`) are **already in the default Trusted
allowlist**, so raising the environment's network level to "Full" would change
nothing. The block comes from the **GitHub proxy**, which (per the
[Claude Code on the web docs](https://code.claude.com/docs/en/claude-code-on-the-web#github-proxy)):

> limits GitHub API and release-asset requests to **repositories attached to the
> session**, regardless of the environment's network access level. Setup scripts
> that download release assets from **unattached repositories return a 403**.
> Committed files from public repositories are fetched through
> `raw.githubusercontent.com`…

That is exactly what we see: schedules are a **committed file** (reachable), while
the play-by-play/roster parquet are **release assets on an unattached repo**
(`nflverse/nflverse-data`) → `403`. Attaching that repo via `add_repo` is the
intended mechanism, but it is a third-party org outside this session's GitHub
scope (`edgecash/velocity`), so it needs a one-time authorization grant first.

## Ways to get EPA data (any one works)

1. **Commit the parquet into an attached repo.** Download
   `play_by_play_<year>.parquet` from nflverse and commit it onto a branch of
   `edgecash/velocity`; it is then served from `raw.githubusercontent.com` (a
   committed file in an attached repo), which is already allowlisted. Read it
   with `pd.read_parquet(<raw url>)` → `normalize_pbp`.
2. **Run locally.** Move the session to a terminal with `claude --teleport`; a
   normal machine has full internet, so `velocity.ingest.nfl.load_pbp` fetches
   nflverse directly with no changes.
3. **Authorize the repo.** Have an admin grant `nflverse/nflverse-data` access to
   the Claude GitHub connection (Claude GitHub settings), then `add_repo` attaches
   it and the proxy serves its release assets.

## What already works without any of the above

- `velocity.ingest.nfl.load_schedules` fetches the games CSV from
  `raw.githubusercontent.com` and normalizes real seasons.
- `scripts/run_real_backtest.py` runs a real walk-forward on that schedule data
  with the schedule-only **scores** ratings (calibration error ≈ 0.025 on 2023).

## The one-line swap once play-by-play is available

`velocity.ingest.nfl.load_pbp` already targets the release-asset URLs directly
(no `nfl_data_py`). Switching the backtest from the scores ratings to the EPA
ratings is a one-line factory change:

```python
# scores-only (works today):
model = ScoresGameModel(fit_scores_ratings(train_games), ...)

# EPA (once play-by-play is reachable):
plays = load_pbp([season])                    # velocity.ingest.nfl
model = NFLGameModel(fit_ratings(train_plays), ...)
```
