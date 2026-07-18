# datasets/ — committed real NFL & NCAAF inputs

The local harness reads real game and play data from here (see
`velocity/ingest/local.py` and `scripts/run_backtest_local.py`). Unlike the
generated `data/` directory, everything under `datasets/` **is committed** (the
`.gitignore` opts it back in), so backtests are reproducible from a clean clone.

## Layout

```
datasets/
  nfl/
    games.(csv|parquet)     # one row per game, with final scores
    plays.(csv|parquet)     # one row per play (EPA)
    lines.(csv|parquet)     # optional: line archive for wagering/CLV
  ncaaf/
    games.(csv|parquet)
    plays.(csv|parquet)
    recruiting.(csv|parquet) # optional: for preseason priors
```

CSV or parquet both work — the reader picks by extension.

## Expected columns (the canonical schema)

Files should map onto the canonical store schema
(`velocity/store/schema.py`). Column **names can differ** in your source — the
loader takes a `rename` map to bridge them, so you don't have to re-shape the
files by hand. The target columns are:

**games** — `game_id`, `season`, `week`, `home_team`, `away_team`,
`home_score`, `away_score`, `kickoff` (a parseable datetime). Optional:
`season_type` (PRE/REG/POST, default REG), `neutral_site` (default False),
`roof`, `surface`. `league` is injected by the loader. Any betting columns you
have (e.g. `spread_line`, `total_line`) are carried through for the wagering
evaluation.

**plays** — `play_id`, `game_id`, `season`, `week`, `posteam`, `defteam`,
`epa`. Optional: `play_type`, `down`, `yards_gained`, `success` (derived from
`epa > 0` when absent).

## How the data is used

- **NFL:** `plays` → opponent-adjusted **EPA ratings** (`fit_ratings`) → the NFL
  game model. This is the full-strength path the schedule-only scores model
  stands in for.
- **NCAAF:** `plays` → ratings, blended with **preseason priors** from
  `recruiting` (+ returning production if present) via Bayesian shrinkage.
- **Wagering / CLV:** if `lines` is present, the walk-forward prices bets against
  it; otherwise it reports projection calibration only.

## Adding your files

Drop them in the folders above. If the column names don't match the target
schema, tell me the source (or let me inspect a file with
`velocity.ingest.local.describe`) and I'll supply the `rename` map — no manual
reshaping needed.
