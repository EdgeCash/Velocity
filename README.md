# Velocity

Game and player-prop projection and wagering system for the four in-season
sports — **NFL, NCAAF, MLB and WNBA** — across three products: game markets
(spread, moneyline, total, team totals), DraftKings DFS, and the prediction
exchanges (Kalshi, Polymarket). NCAAB and NHL ride along in a content-and-CLV
posture.

Velocity projects the full distribution of every game and market — spreads,
totals, moneylines, team totals, and player props — from a shared Monte Carlo
simulation, then converts those projections into disciplined, positive-expected
-value wagers via de-vigging, edge estimation, and fractional-Kelly staking.

The guiding metric is **closing-line value (CLV)**: the market's closing price is
the sharpest widely available forecast, so consistently beating it is the real
signal of edge.

On top of the EV gate sits an **intelligence layer** (`velocity/intel`,
[`docs/INTEL.md`](docs/INTEL.md)): every qualifying bet is judged against the
game's evidence — unit matchups, recent form, rest, and the injury report —
and tiered into argued pick sets. It confirms, demotes, or vetoes; it never
promotes a bet the model didn't like and never touches stakes.

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full system design,
[`docs/BUILD.md`](docs/BUILD.md) for the phased, test-gated build plan, and
[`docs/WAGERING.md`](docs/WAGERING.md) for the wagering system's current state
and build plan.

## The Excel front end

Velocity is the engine; Excel is a front end over it. `velocity/export` writes
six stable-named, stable-shaped CSVs into `datasets/exports/` — games, props,
DFS pool, DFS optimizer, curated plays and a dashboard — which Power Query
reads with no transformation step:

```bash
python -m velocity.run_weekly --slate-dir artifacts/slate   # full week
python -m velocity.run_weekly --steps export                # re-export only, seconds
```

[`docs/WORKFLOW.md`](docs/WORKFLOW.md) is the operating manual — how the
system runs end to end, on whose clock, and what to do about it.

Every run also states whether the board is actually usable — `readiness.csv`,
a colour-coded **Run status** block on the workbook's first tab, and a CI gate
that fails the job when a surface the card needs is missing
([`docs/GAME_DAY.md`](docs/GAME_DAY.md)).

The same run also writes **`velocity.xlsx`** — those six tables as one
prebuilt, formatted workbook. That is the route for Excel on an iPad, iPhone
or Android tablet, none of which have Power Query
([`docs/EXCEL_IPAD.md`](docs/EXCEL_IPAD.md)).

The export layer re-simulates nothing: it is a read-only projection of the
frames the pipeline already banks, so the same numbers reach the workbook, the
site and the cards. Desktop setup and the recommended workbook structure are in
[`docs/EXCEL_SETUP.md`](docs/EXCEL_SETUP.md); the architecture and its gaps in
[`docs/HYBRID_MIGRATION_PLAN.md`](docs/HYBRID_MIGRATION_PLAN.md).

## Layout

```
velocity/
  store/      canonical schema, parquet/duckdb IO, point-in-time access
  features/   opponent-adjusted efficiency, usage, context
  models/     game models (NFL/NCAAF), props, shared Monte Carlo sim
  wagering/   de-vig, edge/EV, Kelly staking, portfolio
  intel/      intelligence layer — matchup/form/rest/injury signals → tiered picks
  dfs/        DK salary ingest, scoring, exact optimizer, GPP portfolios
  export/     read-only projection of the banked frames → Excel-ready CSV
  backtest/   walk-forward engine + metrics
  eval/       calibration + reports
```

## Development

```bash
pip install -e '.[dev]'
pytest          # fast, offline, fixture-backed suite
ruff check .
mypy
```

The test suite never hits the network — it runs on the frozen fixtures under
`tests/fixtures/`. Determinism is enforced via seeded generators
(`velocity/util/seed.py`), so the same seed and input always produce the same
output.
