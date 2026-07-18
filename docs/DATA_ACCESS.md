# Data access — what's reachable and what needs unblocking

**Status:** the model runs end-to-end on real NFL data *except* play-by-play,
which is blocked by network egress policy. This doc records the exact state so an
admin can open the one gap.

## Probe results (from the managed remote environment)

| Data | Host | Result |
|---|---|---|
| Schedules + final scores + closing lines | `raw.githubusercontent.com` | ✅ `200` — reachable |
| Play-by-play (EPA) | `github.com/nflverse/nflverse-data/releases/download/…` | ❌ `403` — egress-blocked |
| Weekly rosters | `github.com/nflverse/nflverse-data/releases/download/…` | ❌ `403` — egress-blocked |
| Release CDN (redirect target) | `objects.githubusercontent.com` | ✅ `404` — host reachable, just needs the signed URL |
| GitHub API (nflverse) | `api.github.com` | ❌ scoped to this repo only |

The play-by-play and roster files are GitHub **release assets**. A request to
`github.com/.../releases/download/...` returns `302` to a signed
`objects.githubusercontent.com` URL — and that CDN host is already reachable. The
egress policy blocks the `github.com` hop before the redirect, so the whole chain
fails with `403`.

## The ask

To enable the full **EPA** projection path, allow outbound HTTPS to:

- **`github.com`** — specifically the `/*/releases/download/*` paths (this is the
  only blocked hop; it redirects to the already-allowed CDN), **or**
- if `github.com` can't be broadly allowed, the release-asset CDN
  **`objects.githubusercontent.com`** *plus* whatever host issues the signed
  redirect.

No credentials are needed — these are public files.

## What already works without any change

- `velocity.ingest.nfl.load_schedules` fetches the games CSV from
  `raw.githubusercontent.com` and normalizes real seasons.
- `scripts/run_real_backtest.py` runs a real walk-forward on that schedule data
  with the schedule-only **scores** ratings.

## What flips on once egress opens

`velocity.ingest.nfl.load_pbp` already targets the release-asset URLs directly
(no `nfl_data_py`). Swapping the backtest's model factory from the scores ratings
to the EPA ratings is a one-line change:

```python
# scores-only (works today):
model = ScoresGameModel(fit_scores_ratings(train_games), ...)

# EPA (once play-by-play is reachable):
plays = load_pbp([season])                    # velocity.ingest.nfl
model = NFLGameModel(fit_ratings(train_plays), ...)
```
